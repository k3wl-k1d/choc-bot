#!/usr/bin/env python3
"""
Pokemon Showdown Replay Analyzer
---------------------------------
Parses .html or .txt replay files and outputs per-Pokemon stats:
  - Damage Dealt   (direct from moves | passive dealt to foe)
  - Damage Taken   (direct from opponent moves | passive self-inflicted)
  - Kills          (fainted opponents)
  - Deaths         (self fainted)

Usage:
    python ps_replay_analyzer.py <replay_file.html|replay_file.txt>
"""

import sys
import re
from html.parser import HTMLParser
from collections import defaultdict


# Extract the raw log text from HTML or TXT
class _BattleLogExtractor(HTMLParser):
    def __init__(self):
        super().__init__()
        self._in_log = False
        self._chunks = []

    def handle_starttag(self, tag, attrs):
        if tag == "script":
            attrs_dict = dict(attrs)
            if attrs_dict.get("class") == "battle-log-data":
                self._in_log = True

    def handle_endtag(self, tag):
        if tag == "script":
            self._in_log = False

    def handle_data(self, data):
        if self._in_log:
            self._chunks.append(data)

    def get_log(self):
        return "".join(self._chunks)


def extract_log(filepath: str) -> str:
    """Load a replay from disk (html or txt)."""
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    return extract_log_from_text(content, filepath)


def extract_log_from_text(content: str, filename: str) -> str:
    """
    Parse a replay from an already-read string.
    Used by Choc, which downloads attachment bytes in memory.
    `filename` is used only to detect .html vs .txt - no file I/O is done.
    """
    if filename.lower().endswith(".html"):
        parser = _BattleLogExtractor()
        parser.feed(content)
        log = parser.get_log()
        if not log.strip():
            raise ValueError("Could not find battle-log-data in the HTML file.")
        log = log.replace("\\/", "/")
        return log
    else:
        # Plain-text replay: already pipe-delimited lines
        return content


# Core analyser

# from-tags where the damage is self-inflicted - no opponent gets dealt credit
# Covers: move recoil, item recoil, and move-specific recoil strings used by Showdown
# Rocky Helmet is NOT here - it has an [of] field and credits the helmet holder
SELF_INFLICTED_TAGS = {
    "Recoil",
    "recoil",
    "item: Life Orb",
    "item: Black Sludge",
    "item: Sticky Barb",
    "item: Flame Orb",
    "item: Toxic Orb",
    "steelbeam",
    "mindblown",
    "move: Curse",
    "move: Substitute",
}

# Passive damage taken (opponent-sourced, indirect)
PASSIVE_SELF_TAGS = {
    "brn", "psn", "tox", "confusion", "Salt Cure", "Leech Seed",
    "weather: Sandstorm", "weather: Hail",
    "hazard: Spikes", "hazard: Stealth Rock", "hazard: Toxic Spikes",
    "move: Future Sight", "move: Doom Desire",
    "move: Infestation", "move: Bind", "move: Wrap", "move: Fire Spin",
    "move: Whirlpool", "move: Magma Storm", "move: Sand Tomb",
    "move: Thunder Cage", "move: Clamp", "move: Snap Trap",

    "ability: Innards Out", "ability: Iron Barbs", "ability: Aftermath", "ability: Rough Skin",
}

# Trapping move from-tags - credited to whoever used the move, tracked via trapping_inflicted_by
TRAPPING_MOVE_TAGS = {
    "[from] move: Infestation", "[from] move: Bind", "[from] move: Wrap",
    "[from] move: Fire Spin", "[from] move: Whirlpool", "[from] move: Magma Storm",
    "[from] move: Sand Tomb", "[from] move: Thunder Cage", "[from] move: Clamp",
    "[from] move: Snap Trap",
}

# Status inflicted by the holder's own item - never credit the opponent.
SELF_INFLICTED_STATUS_ITEMS = {"item: Flame Orb", "item: Toxic Orb"}


def parse_hp(hp_str: str):
    """
    Parse HP strings like '195/280', '60/100', '0 fnt', '100/100', '100 psn'.
    Returns (current, max) as floats. max may be None if format is percentage.
    """
    hp_str = hp_str.strip()
    if "fnt" in hp_str:
        return 0, None
    # Strip status conditions appended to HP i.e. "172/280 psn", "100 brn"
    hp_str = hp_str.split(" ")[0]
    if "/" in hp_str:
        parts = hp_str.split("/")
        return float(parts[0]), float(parts[1])
    return float(hp_str), None


def slot_to_name(slot_label: str):
    """
    'p1a: Gyarados'     -> ('p1', 'Gyarados')
    'p2b: Scream Tail'  -> ('p2', 'Scream Tail')
    'p1a: World B Flat' -> ('p1', 'World B Flat')  ← nickname, resolved by caller
    """
    m = re.match(r"(p[12])[ab]:\s*(.+)", slot_label)
    if m:
        return m.group(1), m.group(2)
    return None, slot_label


def species_from_info(mon_info: str) -> str:
    """
    Extract the species name from the mon_info field of a switch line.
    i.e. 'Sigilyph, M'     -> 'Sigilyph'
         'Lopunny-Mega, F'  -> 'Lopunny-Mega'
         'Articuno-Galar'   -> 'Articuno-Galar'
         'Great Tusk'       -> 'Great Tusk'
    The species is always the first comma-separated token.
    """
    return mon_info.split(",")[0].strip()


def analyse(log_text: str) -> dict:
    """
    Returns a nested dict:
    {
      'p1': { pokemon_name: { 'direct_dealt': float, 'passive_dealt': float,
                               'direct_taken': float, 'passive_taken': float,
                               'kills': int, 'deaths': int } },
      'p2': { ... },
      'players': { 'p1': str, 'p2': str },
      'team_order': { 'p1': [species, ...], 'p2': [species, ...] },
      'winner': str | None,
    }
    """
    lines = log_text.splitlines()

    players = {}   # p1/p2 -> player name
    winner = None

    # Team preview order - list of species in the order Showdown shows them
    # at team preview. Used purely for output sorting; no effect on calculations.
    team_order = {"p1": [], "p2": []}

    # Track the active pokemon per slot
    active = {}          # 'p1' / 'p2' -> species name currently in slot a
    # Map nickname -> species so all later log lines resolve correctly
    nickname_map = {}    # (player, nickname) -> species name
    # Track current HP for each pokemon name (as percentage 0-100)
    hp = defaultdict(lambda: 100.0)   # species_name -> current HP %
    max_hp = {}          # species_name -> max HP (raw, if available)

    stats = defaultdict(lambda: defaultdict(lambda: {
        "direct_dealt": 0.0,
        "passive_dealt": 0.0,
        "direct_taken": 0.0,
        "passive_taken": 0.0,
        "kills": 0,
        "deaths": 0,
    }))

    # Keep a small look-ahead buffer: damage events need to know
    # who just used a move so it can be attributed with the damage.
    last_move_user = None   # (player, species) that last used a move

    # Track the from_tag of the most recent damage event per (player, mon).
    # Used in the faint block to know whether the killing hit was a move,
    # status, or hazard - without being confused by recoil on the attacker.
    last_hit_from_tag = {}  # (player, mon) -> from_tag string of last damage

    # The Substitute move shows up as:
    #   |move|...|Substitute|...
    #   |-start|...|Substitute
    #   |-damage|...|76/100       <- self-cost, no [from] tag
    # We must skip that damage line so it isn't credited to the opponent.
    skip_next_damage_as_sub_cost = set()  # set of (player, mon)

    # Track who applied each status to each Pokemon so passive damage
    # (burn, poison, toxic) is credited to the inflicter, not the current active mon.
    # key: (player, mon)  value: (opponent_player, inflicter_species)
    status_inflicted_by = {}

    # Track who applied a trapping move so chip ticks credit the trapper off-field.
    trapping_inflicted_by = {}  # (player, mon) -> (trapper_player, trapper_species)

    # Track who set each entry hazard on each side so hazard chip damage
    # (Spikes, Stealth Rock, Toxic Spikes) is credited to the setter.
    # key: (player_side_suffering, hazard_tag)  value: (opponent_player, setter_species)
    hazard_set_by = {}

    # Track whether the last hit on each (player, mon) was self-inflicted.
    last_hit_self_inflicted = {}  # (player, mon) -> bool

    # Passive from-tags that should credit status_inflicted_by instead of active mon
    STATUS_DAMAGE_TAGS = {"[from] psn", "[from] tox", "[from] brn"}
    # Passive from-tags that should credit hazard_set_by instead of active mon
    HAZARD_DAMAGE_TAGS = {
        "[from] Spikes", "[from] hazard: Spikes",
        "[from] Stealth Rock", "[from] hazard: Stealth Rock",
        "[from] Toxic Spikes", "[from] hazard: Toxic Spikes",
    }

    def record_damage(target_player, target_mon, amount, is_passive,
                      dealer=None, is_self_inflicted=False):
        """
        Record damage taken by target_mon and credit dealt-damage to dealer.
        dealer: optional (player, species) for explicit attribution (status/hazard).
        is_self_inflicted: if True, no opponent gets dealt credit at all.
        """
        opponent = "p2" if target_player == "p1" else "p1"

        # Self-inflicted: only passive_taken, no opponent credit.
        if is_self_inflicted:
            stats[target_player][target_mon]["passive_taken"] += amount
            return

        # Damage taken by target
        if is_passive:
            stats[target_player][target_mon]["passive_taken"] += amount
        else:
            stats[target_player][target_mon]["direct_taken"] += amount

        # Attribute dealt
        if dealer:
            dealer_player, dealer_mon = dealer
            stats[dealer_player][dealer_mon]["passive_dealt"] += amount
        elif is_passive:
            dealer_mon = active.get(opponent)
            if dealer_mon:
                stats[opponent][dealer_mon]["passive_dealt"] += amount
        else:
            if last_move_user and last_move_user[0] == opponent:
                dealer_mon = last_move_user[1]
                stats[opponent][dealer_mon]["direct_dealt"] += amount
            else:
                dealer_mon = active.get(opponent)
                if dealer_mon:
                    stats[opponent][dealer_mon]["direct_dealt"] += amount

    def resolve(player, name):
        """Return the species name for a given player+name (which may be a nickname)."""
        return nickname_map.get((player, name), name)

    for line in lines:
        line = line.rstrip()
        if not line.startswith("|"):
            continue
        parts = line.split("|")
        # parts[0] is empty string before first |
        if len(parts) < 2:
            continue
        cmd = parts[1]

        # Player identification
        if cmd == "player" and len(parts) >= 4:
            slot = parts[2]   # 'p1' or 'p2'
            name = parts[3]
            if name:           # ignore empty-name lines that appear mid-replay
                players[slot] = name

        # Team preview - record the species order shown to both players.
        # Format: |poke|p1|Pikachu, F|   (gender is optional)
        # Only capture the species (first comma-separated token), 
        
        elif cmd == "poke" and len(parts) >= 4:
            slot = parts[2]   # 'p1' or 'p2'
            species = species_from_info(parts[3])
            if slot in team_order and species and species not in team_order[slot]:
                team_order[slot].append(species)

        # Switch / Drag (update active slot)
        elif cmd in ("switch", "drag", "replace") and len(parts) >= 4:
            slot_label = parts[2]
            mon_info   = parts[3]  
            hp_str     = parts[4] if len(parts) > 4 else "100/100"

            player, nickname = slot_to_name(slot_label)
            species = species_from_info(mon_info)   # always the real species name

            if player:
                # If this species is already known as a mega/forme alias, resolve
                # it back to the base species so stats stay on one row.
                # e.g. 'Lopunny-Mega' → 'Lopunny' after the first detailschange.
                species = nickname_map.get((player, species), species)
                # Map nickname -> species
                nickname_map[(player, nickname)] = species
                active[player] = species

            mon = species  # use species as the canonical key throughout

            # Record initial HP on switch-in
            cur, mx = parse_hp(hp_str)
            if mx:
                max_hp[mon] = mx
                hp[mon] = (cur / mx) * 100
            else:
                hp[mon] = cur  # already percentage

            # Ensure this pokemon appears in stats
            _ = stats[player][mon]

        # Forme / mega evolution change
        # e.g. |detailschange|p2a: Bunny Gesserit|Lopunny-Mega, F
        # The Pokemon is the same individual - alias the mega name back to the
        # base species so all subsequent damage lines stay on one row.
        elif cmd == "detailschange" and len(parts) >= 4:
            slot_label   = parts[2]   # 'p2a: Bunny Gesserit'
            new_info     = parts[3]   # 'Lopunny-Mega, F'
            player, nickname = slot_to_name(slot_label)
            if player:
                new_species  = species_from_info(new_info)
                base_species = resolve(player, nickname)   # already tracked as
                # Point the mega name back to the base species key so any line
                # that references the new forme name resolves to the same entry.
                nickname_map[(player, new_species)] = base_species
                # active slot is still the same mon, no change needed

        # Move (track last move user)
        elif cmd == "move" and len(parts) >= 3:
            slot_label = parts[2]
            player, name = slot_to_name(slot_label)
            if player:
                last_move_user = (player, resolve(player, name))

        # Turn boundary - reset move tracking so last_move_user never
        # bleeds across turns and pollutes status/damage attribution.
        elif cmd == "turn":
            last_move_user = None

        # Status applied (poison/burn/etc.)
        # Track who inflicted the status so future chip damage is credited correctly.
        # Self-inflicted statuses (Flame Orb / Toxic Orb) are skipped entirely.
        elif cmd == "-status" and len(parts) >= 4:
            slot_label  = parts[2]
            status_type = parts[3]
            player, name = slot_to_name(slot_label)
            if player and status_type in ("psn", "tox", "brn"):
                # Check for a self-inflicted orb status
                from_tag = parts[4] if len(parts) > 4 else ""
                if any(tag in from_tag for tag in SELF_INFLICTED_STATUS_ITEMS):
                    # Don't set an inflicter - burn/poison damage will be self-inflicted
                    pass
                else:
                    mon = resolve(player, name)
                    opponent = "p2" if player == "p1" else "p1"

                    # Priority 1: Toxic Spikes caused the status
                    if hazard_set_by.get((player, "[from] Toxic Spikes")):
                        inflicter = hazard_set_by[(player, "[from] Toxic Spikes")]
                    # Priority 2: an opponent move this turn inflicted the status
                    elif last_move_user and last_move_user[0] == opponent:
                        inflicter = last_move_user
                    # Priority 3: fall back to active opponent
                    else:
                        inflicter_mon = active.get(opponent)
                        inflicter = (opponent, inflicter_mon) if inflicter_mon else None

                    if inflicter:
                        status_inflicted_by[(player, mon)] = inflicter

        # Hazard set (Spikes / Stealth Rock / Toxic Spikes)
        # The move line just before this tells us the setter.
        elif cmd == "-sidestart" and len(parts) >= 4:
            # parts[2] = 'p1: Shirmp' - extract the player side
            side_player = parts[2].split(":")[0].strip()   # 'p1' or 'p2'
            hazard_info = parts[3]   # i.e. 'move: Toxic Spikes', 'Spikes'
            # Normalise to a tag that will appear in damage lines
            if "Toxic Spikes" in hazard_info:
                hazard_tag = "[from] Toxic Spikes"
            elif "Spikes" in hazard_info:
                hazard_tag = "[from] Spikes"
            elif "Stealth Rock" in hazard_info:
                hazard_tag = "[from] Stealth Rock"
            else:
                hazard_tag = None
            if hazard_tag and last_move_user:
                # last_move_user is the setter (they just used the hazard move)
                hazard_set_by[(side_player, hazard_tag)] = last_move_user

        # Hazard cleared (Rapid Spin / Defog)
        elif cmd == "-sideend" and len(parts) >= 4:
            side_player = parts[2].split(":")[0].strip()
            hazard_info = parts[3]
            if "Toxic Spikes" in hazard_info:
                hazard_set_by.pop((side_player, "[from] Toxic Spikes"), None)
            elif "Spikes" in hazard_info:
                hazard_set_by.pop((side_player, "[from] Spikes"), None)
            elif "Stealth Rock" in hazard_info:
                hazard_set_by.pop((side_player, "[from] Stealth Rock"), None)

        # Substitute started - the very next -damage on this mon is the
        # HP cost of creating the sub, not an attack. Flag it to skip.
        elif cmd == "-start" and len(parts) >= 4:
            if parts[3] == "Substitute":
                slot_label = parts[2]
                player, name = slot_to_name(slot_label)
                if player:
                    skip_next_damage_as_sub_cost.add((player, resolve(player, name)))

        # Trapping move activated (Infestation, Bind, etc.) - record the trapper.
        # |-activate|p2a: Tapu Fini|move: Infestation|[of] p1a: charlotte
        elif cmd == "-activate" and len(parts) >= 4:
            move_info = parts[3]
            if any(tag.replace("[from] ", "") in move_info for tag in TRAPPING_MOVE_TAGS):
                slot_label = parts[2]
                of_tag = parts[4] if len(parts) > 4 else ""
                player, name = slot_to_name(slot_label)
                of_player, of_name = slot_to_name(of_tag.replace("[of] ", ""))
                if player and of_player:
                    mon = resolve(player, name)
                    trapper = (of_player, resolve(of_player, of_name))
                    trapping_inflicted_by[(player, mon)] = trapper

        # Heal - update hp so subsequent damage calculations have the right baseline.
        # Covers: Leftovers, Wish, Drain moves, Soft-Boiled, Recover, etc.
        elif cmd == "-heal" and len(parts) >= 4:
            slot_label = parts[2]
            hp_str     = parts[3]
            player, name = slot_to_name(slot_label)
            if not player:
                continue
            mon = resolve(player, name)
            cur, mx = parse_hp(hp_str)
            if mx:
                hp[mon] = (cur / mx) * 100
            else:
                hp[mon] = cur

        # Damage
        elif cmd == "-damage" and len(parts) >= 4:
            slot_label = parts[2]
            hp_str     = parts[3]
            from_tag   = parts[4] if len(parts) > 4 else ""
            of_tag     = parts[5] if len(parts) > 5 else ""

            player, name = slot_to_name(slot_label)
            if not player:
                continue
            mon = resolve(player, name)

            # Substitute HP cost - self-inflicted passive_taken, no opponent credit.
            if (player, mon) in skip_next_damage_as_sub_cost:
                skip_next_damage_as_sub_cost.discard((player, mon))
                cur, mx = parse_hp(hp_str)
                new_pct_sub = (cur / mx) * 100 if mx else cur
                sub_cost = hp.get(mon, 100.0) - new_pct_sub
                if sub_cost > 0:
                    stats[player][mon]["passive_taken"] += sub_cost
                hp[mon] = new_pct_sub
                continue

            # Compute damage percentage
            cur, mx = parse_hp(hp_str)
            if mx:
                new_pct = (cur / mx) * 100
            else:
                new_pct = cur

            prev_pct = hp.get(mon, 100.0)
            damage_pct = prev_pct - new_pct
            if damage_pct < 0:
                damage_pct = 0
            hp[mon] = new_pct

            # Rocky Helmet: damage to attacker, credited as passive_dealt to the holder.
            # The [of] field names the holder's slot, so attribute directly.
            if "item: Rocky Helmet" in from_tag:
                of_player, of_name = slot_to_name(of_tag.replace("[of] ", ""))
                if of_player:
                    holder = resolve(of_player, of_name)
                    stats[player][mon]["passive_taken"] += damage_pct
                    stats[of_player][holder]["passive_dealt"] += damage_pct
                    last_hit_from_tag[(player, mon)] = from_tag
                    last_hit_self_inflicted[(player, mon)] = False
                continue

            # Determine if self-inflicted (recoil, orb damage, etc.)
            is_self_inflicted = any(tag in from_tag for tag in SELF_INFLICTED_TAGS)

            # Orb-caused burn/poison chip is also self-inflicted (no inflicter was set)
            if not is_self_inflicted and from_tag in STATUS_DAMAGE_TAGS:
                if (player, mon) not in status_inflicted_by:
                    is_self_inflicted = True

            # Determine if passive (opponent-sourced but indirect)
            is_passive = any(tag in from_tag for tag in PASSIVE_SELF_TAGS)

            # Resolve explicit dealer for status/hazard/trapping chip damage.
            dealer = None
            if not is_self_inflicted:
                if from_tag in STATUS_DAMAGE_TAGS:
                    dealer = status_inflicted_by.get((player, mon))
                elif from_tag in HAZARD_DAMAGE_TAGS:
                    dealer = hazard_set_by.get((player, from_tag))
                elif from_tag in TRAPPING_MOVE_TAGS:
                    dealer = trapping_inflicted_by.get((player, mon))

            record_damage(player, mon, damage_pct, is_passive,
                          dealer=dealer, is_self_inflicted=is_self_inflicted)
            last_hit_from_tag[(player, mon)] = from_tag
            last_hit_self_inflicted[(player, mon)] = is_self_inflicted

        # Faint
        elif cmd == "faint" and len(parts) >= 3:
            slot_label = parts[2]
            player, name = slot_to_name(slot_label)
            if not player:
                continue
            mon = resolve(player, name)

            stats[player][mon]["deaths"] += 1

            opponent = "p2" if player == "p1" else "p1"

            killing_tag = last_hit_from_tag.get((player, mon), "")
            killing_self = last_hit_self_inflicted.get((player, mon), False)

            if killing_self:
                # Self-inflicted death (recoil, orb) - no kill credit
                pass
            elif killing_tag in STATUS_DAMAGE_TAGS:
                # Killed by burn/poison - credit the original inflicter
                killer_entry = status_inflicted_by.get((player, mon))
                if killer_entry:
                    stats[killer_entry[0]][killer_entry[1]]["kills"] += 1
                else:
                    killer = active.get(opponent)
                    if killer:
                        stats[opponent][killer]["kills"] += 1
            elif killing_tag in HAZARD_DAMAGE_TAGS:
                # Killed by entry hazard - credit the setter
                killer_entry = hazard_set_by.get((player, killing_tag))
                if killer_entry:
                    stats[killer_entry[0]][killer_entry[1]]["kills"] += 1
                else:
                    killer = active.get(opponent)
                    if killer:
                        stats[opponent][killer]["kills"] += 1
            else:
                # Killed by a direct move - credit whoever is active on the other side
                killer = active.get(opponent)
                if killer:
                    stats[opponent][killer]["kills"] += 1

            hp[mon] = 0
            status_inflicted_by.pop((player, mon), None)
            last_hit_from_tag.pop((player, mon), None)
            last_hit_self_inflicted.pop((player, mon), None)

        # Winner 
        elif cmd == "win" and len(parts) >= 3:
            winner = parts[2]

    return {
        "p1": dict(stats["p1"]),
        "p2": dict(stats["p2"]),
        "players": players,
        "team_order": team_order,
        "winner": winner,
    }


# Formatters
def _ordered_mons(side: str, results: dict):
    """
    Yield (mon_name, stats_dict) pairs for one player's side in team-preview order.
    Any mons that somehow aren't in team preview (shouldn't happen in a normal
    replay, but defensive) are appended at the end in alphabetical order.
    """
    mons       = results[side]
    team_order = results.get("team_order", {}).get(side, [])

    seen = set()
    for species in team_order:
        if species in mons:
            yield species, mons[species]
            seen.add(species)
    # Fallback: any tracked mon that wasn't in team preview
    for species in sorted(mons):
        if species not in seen:
            yield species, mons[species]


def _build_side_block(side: str, results: dict) -> str:
    """
    Return a monospace Discord block for one player's side.
    Uses a code block so columns line up in any Discord client.
    """
    players  = results["players"]
    mons     = results[side]
    pname    = players.get(side, side)

    lines = []
    lines.append(f"  {'Pokémon':<20} {'DD':>7} {'PD':>7} {'DT':>7} {'PT':>7} {'K':>3} {'D':>3}")
    lines.append(f"  {'-'*20} {'-'*7} {'-'*7} {'-'*7} {'-'*7} {'-'*3} {'-'*3}")

    total = {k: 0.0 for k in
             ("direct_dealt", "passive_dealt", "direct_taken", "passive_taken",
              "kills", "deaths")}

    for mon, s in _ordered_mons(side, results):
        dd = s["direct_dealt"]
        pd = s["passive_dealt"]
        dt = s["direct_taken"]
        pt = s["passive_taken"]
        k  = s["kills"]
        d  = s["deaths"]
        lines.append(
            f"  {mon:<20} {dd:>6.1f}% {pd:>6.1f}% {dt:>6.1f}% {pt:>6.1f}% {k:>3} {d:>3}"
        )
        for key in total:
            total[key] += s[key]

    lines.append(f"  {'─'*20} {'─'*7} {'─'*7} {'─'*7} {'─'*7} {'─'*3} {'─'*3}")
    lines.append(
        f"  {'TOTAL':<20} {total['direct_dealt']:>6.1f}% "
        f"{total['passive_dealt']:>6.1f}% "
        f"{total['direct_taken']:>6.1f}% "
        f"{total['passive_taken']:>6.1f}% "
        f"{total['kills']:>3.0f} {total['deaths']:>3.0f}"
    )

    header = f"**{pname}** ({side})\n```\n" + "\n".join(lines) + "\n```"
    return header


def format_for_discord(results: dict) -> list[str]:
    """
    Build the full !analyze Discord output.
    Returns a list of message strings (split to stay under Discord's 2000-char limit).
    Each element should be sent as a separate message.
    """
    players = results["players"]
    winner  = results["winner"]
    p1name  = players.get("p1", "Player 1")
    p2name  = players.get("p2", "Player 2")

    # Header message
    if winner:
        trophy = "🏆"
        if winner == p1name:
            header = f"{trophy} **{p1name}** defeated **{p2name}**"
        elif winner == p2name:
            header = f"{trophy} **{p2name}** defeated **{p1name}**"
        else:
            header = f"{trophy} Winner: **{winner}**"
    else:
        header = f"**{p1name}** vs **{p2name}**"

    legend = (
        "```\n"
        "DD = Direct Dealt  |  PD = Passive Dealt\n"
        "DT = Direct Taken  |  PT = Passive Taken\n"
        "K  = Kills         |  D  = Deaths\n"
        "(all damage values = % of target's max HP)\n"
        "```"
    )

    messages = [
        f"**Replay Analysis** - {header}\n{legend}",
        _build_side_block("p1", results),
        _build_side_block("p2", results),
    ]
    return messages


def print_results(results: dict):
    """CLI pretty-printer (unchanged - still works for standalone use)."""
    players = results["players"]
    winner  = results["winner"]

    print("=" * 65)
    print("  POKEMON SHOWDOWN REPLAY ANALYSIS")
    print("=" * 65)
    if winner:
        print(f"  Winner: {winner}")
    print()

    for side in ("p1", "p2"):
        player_name = players.get(side, side)
        print(f"{'─'*65}")
        print(f"  {player_name.upper()}  ({side})")
        print(f"{'─'*65}")

        mons = results[side]
        if not mons:
            print("  (no data)")
            print()
            continue

        # Header
        print(f"  {'Pokemon':<22} {'Dir.Dealt':>10} {'Pas.Dealt':>10} "
              f"{'Dir.Taken':>10} {'Pas.Taken':>10} {'Kills':>6} {'Deaths':>7}")
        print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10} {'-'*10} {'-'*6} {'-'*7}")

        total = {k: 0.0 for k in
                 ("direct_dealt", "passive_dealt", "direct_taken", "passive_taken",
                  "kills", "deaths")}

        for mon, s in _ordered_mons(side, results):
            dd = s["direct_dealt"]
            pd = s["passive_dealt"]
            dt = s["direct_taken"]
            pt = s["passive_taken"]
            k  = s["kills"]
            d  = s["deaths"]
            print(f"  {mon:<22} {dd:>9.1f}% {pd:>9.1f}% "
                  f"{dt:>9.1f}% {pt:>9.1f}% {k:>6} {d:>7}")
            for key in total:
                total[key] += s[key]

        print(f"  {'─'*22} {'─'*10} {'─'*10} {'─'*10} {'─'*10} {'─'*6} {'─'*7}")
        print(f"  {'TEAM TOTAL':<22} {total['direct_dealt']:>9.1f}% "
              f"{total['passive_dealt']:>9.1f}% "
              f"{total['direct_taken']:>9.1f}% "
              f"{total['passive_taken']:>9.1f}% "
              f"{total['kills']:>6.0f} {total['deaths']:>7.0f}")
        print()

    print("=" * 65)
    print("  Notes:")
    print("  • Damage values are in % of the target's max HP")
    print("  • Direct Dealt / Taken: from moves")
    print("  • Passive Dealt / Taken: Life Orb, hazards, burn/poison,")
    print("    weather, recoil, Leech Seed, Salt Cure, etc.")
    print("=" * 65)




def main():
    if len(sys.argv) < 2:
        print("Usage: python ps_replay_analyzer.py <replay_file.html|.txt>")
        sys.exit(1)

    filepath = sys.argv[1]
    try:
        log = extract_log(filepath)
    except FileNotFoundError:
        print(f"Error: File not found: {filepath}")
        sys.exit(1)
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)

    results = analyse(log)
    print_results(results)


if __name__ == "__main__":
    main()