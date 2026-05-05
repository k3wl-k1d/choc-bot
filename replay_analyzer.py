#!/usr/bin/env python3
"""
Pokemon Showdown Replay Analyzer
---------------------------------
Parses .html or .txt replay files and outputs per-Pokemon stats:
  - Damage Dealt   (direct from moves | passive i.e. Life Orb recoil dealt to foe)
  - Damage Taken   (direct from opponent moves | passive i.e. Life Orb recoil to self)
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
    Used by the Discord bot, which downloads attachment bytes in memory.
    `filename` is used only to detect .html vs .txt — no file I/O is done.
    """
    if filename.lower().endswith(".html"):
        parser = _BattleLogExtractor()
        parser.feed(content)
        log = parser.get_log()
        if not log.strip():
            raise ValueError("Could not find battle-log-data in the HTML file.")
        # The HTML escapes forward slashes as \/ — unescape them
        log = log.replace("\\/", "/")
        return log
    else:
        # Plain-text replay: already pipe-delimited lines
        return content


# Core analyser
# Passive damage sources that count as "passive" rather than "direct"
PASSIVE_SELF_TAGS = {
    "item: Life Orb",       # recoil on the user
    "item: Black Sludge",
    "item: Sticky Barb",
    "recoil",               # generic recoil
    "Recoil",
    "move: Curse",
    "move: Substitute",
    "item: Flame Orb",
    "item: Toxic Orb",
    "brn",                  # burn
    "psn",                  # poison
    "tox",                  # bad poison (toxic)
    "confusion",
    "Salt Cure",
    "Leech Seed",
    "weather: Sandstorm",
    "weather: Hail",
    "weather: Snow",
    "hazard: Spikes",
    "hazard: Stealth Rock",
    "hazard: Toxic Spikes",
    "move: Future Sight",   # passive-style delayed
    "move: Doom Desire",
}

# Sources that count as passive damage *dealt* to the foe
PASSIVE_DEALT_TAGS = {
    "drain",        # Leech Life / Giga Drain heals attacker, but the damage itself
                    # is still direct; "drain" appears on the heal line, not the damage line
    "Leech Seed",
    "Salt Cure",
    "brn",
    "psn",
    "tox",
    "confusion",
    "weather: Sandstorm",
    "weather: Hail",
    "weather: Snow",
    "hazard: Spikes",
    "hazard: Stealth Rock",
    "hazard: Toxic Spikes",
}


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
      'winner': str | None,
    }
    """
    lines = log_text.splitlines()

    players = {}   # p1/p2 -> player name
    winner = None

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
    # who just used a move so we can attribute the damage.
    last_move_user = None   # (player, species) that last used a move

    # Track the from_tag of the most recent damage event per (player, mon).
    # Used in the faint block to know whether the killing hit was a move,
    # status, or hazard — without being confused by recoil on the attacker.
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

    # Track who set each entry hazard on each side so hazard chip damage
    # (Spikes, Stealth Rock, Toxic Spikes) is credited to the setter.
    # key: (player_side_suffering, hazard_tag)  value: (opponent_player, setter_species)
    hazard_set_by = {}

    # Passive from-tags that should credit status_inflicted_by instead of active mon
    STATUS_DAMAGE_TAGS = {"[from] psn", "[from] tox", "[from] brn"}
    # Passive from-tags that should credit hazard_set_by instead of active mon
    HAZARD_DAMAGE_TAGS = {
        "[from] Spikes", "[from] hazard: Spikes",
        "[from] Stealth Rock", "[from] hazard: Stealth Rock",
        "[from] Toxic Spikes", "[from] hazard: Toxic Spikes",
    }

    def record_damage(target_player, target_mon, amount, is_passive, dealer=None):
        """
        Record damage taken by target_mon and credit dealt-damage to dealer.
        dealer: optional (player, species) tuple for explicit attribution
                (used for status/hazard damage where the inflicter may be off-field).
                If None, falls back to last_move_user then active opponent.
        """
        opponent = "p2" if target_player == "p1" else "p1"

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
        # The Pokemon is the same individual — alias the mega name back to the
        # base species so all subsequent damage lines stay on one row.
        elif cmd == "detailschange" and len(parts) >= 4:
            slot_label   = parts[2]   # 'p2a: Bunny Gesserit'
            new_info     = parts[3]   # 'Lopunny-Mega, F'
            player, nickname = slot_to_name(slot_label)
            if player:
                new_species  = species_from_info(new_info)
                base_species = resolve(player, nickname)   # what we already track it as
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

        # Turn boundary — reset move tracking so last_move_user never
        # bleeds across turns and pollutes status/damage attribution.
        elif cmd == "turn":
            last_move_user = None

        # Status applied (poison/burn/etc.)
        # Credit the inflicter (last move user from the opponent) so that
        # future [from] psn / [from] brn damage goes to them, not whoever
        # happens to be active when the chip fires.
        elif cmd == "-status" and len(parts) >= 4:
            slot_label  = parts[2]
            status_type = parts[3]   # 'psn', 'tox', 'brn', 'par', etc.
            player, name = slot_to_name(slot_label)
            if player and status_type in ("psn", "tox", "brn"):
                mon = resolve(player, name)
                opponent = "p2" if player == "p1" else "p1"

                # Priority 1: Toxic Spikes on this side caused the status
                # (switch-in poison always comes from the hazard, never a move
                #  on the same turn — so check this before last_move_user)
                if hazard_set_by.get((player, "[from] Toxic Spikes")):
                    inflicter = hazard_set_by[(player, "[from] Toxic Spikes")]
                # Priority 2: an opponent move this turn inflicted the status
                elif last_move_user and last_move_user[0] == opponent:
                    inflicter = last_move_user
                # Priority 3: fall back to whoever is currently active
                else:
                    inflicter_mon = active.get(opponent)
                    inflicter = (opponent, inflicter_mon) if inflicter_mon else None

                if inflicter:
                    status_inflicted_by[(player, mon)] = inflicter

        # Hazard set (Spikes / Stealth Rock / Toxic Spikes)
        # The move line just before this tells us the setter.
        elif cmd == "-sidestart" and len(parts) >= 4:
            # parts[2] = 'p1: Shirmp' — extract the player side
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

        # Substitute started — the very next -damage on this mon is the
        # HP cost of creating the sub, not an attack. Flag it to skip.
        elif cmd == "-start" and len(parts) >= 4:
            if parts[3] == "Substitute":
                slot_label = parts[2]
                player, name = slot_to_name(slot_label)
                if player:
                    skip_next_damage_as_sub_cost.add((player, resolve(player, name)))

        # Heal — update hp so subsequent damage calculations have the right baseline.
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

            player, name = slot_to_name(slot_label)
            if not player:
                continue
            mon = resolve(player, name)

            # Skip the HP cost of creating a Substitute — it is self-inflicted
            # and must not be credited to the opponent as dealt damage.
            if (player, mon) in skip_next_damage_as_sub_cost:
                skip_next_damage_as_sub_cost.discard((player, mon))
                cur, mx = parse_hp(hp_str)
                hp[mon] = (cur / mx) * 100 if mx else cur  # still keep hp current
                continue

            # Compute damage percentage
            cur, mx = parse_hp(hp_str)
            if mx:
                new_pct = (cur / mx) * 100
            else:
                new_pct = cur  # percentage mode

            prev_pct = hp.get(mon, 100.0)
            damage_pct = prev_pct - new_pct
            if damage_pct < 0:
                damage_pct = 0
            hp[mon] = new_pct

            # Determine if passive
            is_passive = any(tag in from_tag for tag in PASSIVE_SELF_TAGS)

            # Resolve explicit dealer for status/hazard chip damage so the
            # credit goes to the original inflicter even if they're off-field.
            dealer = None
            if from_tag in STATUS_DAMAGE_TAGS:
                dealer = status_inflicted_by.get((player, mon))
            elif from_tag in HAZARD_DAMAGE_TAGS:
                dealer = hazard_set_by.get((player, from_tag))

            record_damage(player, mon, damage_pct, is_passive, dealer=dealer)
            # Record the from_tag of this hit keyed to the target, so the faint
            # block knows what actually killed this mon (not affected by recoil
            # or other damage landing on a different mon afterwards).
            last_hit_from_tag[(player, mon)] = from_tag

        # Faint
        elif cmd == "faint" and len(parts) >= 3:
            slot_label = parts[2]
            player, name = slot_to_name(slot_label)
            if not player:
                continue
            mon = resolve(player, name)

            stats[player][mon]["deaths"] += 1

            opponent = "p2" if player == "p1" else "p1"

            # Look up the from_tag of the hit that actually killed this mon.
            killing_tag = last_hit_from_tag.get((player, mon), "")

            if killing_tag in STATUS_DAMAGE_TAGS:
                # Killed by burn/poison — credit the original inflicter
                killer_entry = status_inflicted_by.get((player, mon))
                if killer_entry:
                    stats[killer_entry[0]][killer_entry[1]]["kills"] += 1
                else:
                    killer = active.get(opponent)
                    if killer:
                        stats[opponent][killer]["kills"] += 1
            elif killing_tag in HAZARD_DAMAGE_TAGS:
                # Killed by entry hazard — credit the setter
                killer_entry = hazard_set_by.get((player, killing_tag))
                if killer_entry:
                    stats[killer_entry[0]][killer_entry[1]]["kills"] += 1
                else:
                    killer = active.get(opponent)
                    if killer:
                        stats[opponent][killer]["kills"] += 1
            else:
                # Killed by a direct move — credit whoever is active on the other side
                killer = active.get(opponent)
                if killer:
                    stats[opponent][killer]["kills"] += 1

            hp[mon] = 0
            # Clean up per-mon tracking
            status_inflicted_by.pop((player, mon), None)
            last_hit_from_tag.pop((player, mon), None)

        # Winner 
        elif cmd == "win" and len(parts) >= 3:
            winner = parts[2]

    return {
        "p1": dict(stats["p1"]),
        "p2": dict(stats["p2"]),
        "players": players,
        "winner": winner,
    }


# Formatters
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

    for mon, s in sorted(mons.items()):
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
        f"**Replay Analysis** — {header}\n{legend}",
        _build_side_block("p1", results),
        _build_side_block("p2", results),
    ]
    return messages


def print_results(results: dict):
    """CLI pretty-printer (unchanged — still works for standalone use)."""
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

        for mon, s in sorted(mons.items()):
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