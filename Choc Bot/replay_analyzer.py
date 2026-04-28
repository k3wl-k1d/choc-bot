#!/usr/bin/env python3
"""
Pokemon Showdown Replay Analyzer
--------------------------------
Parses .html or .txt replay files and outputs per-Pokemon stats:
  - Damage Dealt   (direct from moves | passive e.g. Life Orb recoil dealt to foe)
  - Damage Taken   (direct from opponent moves | passive e.g. Life Orb recoil to self)
  - Kills          (fainted opponents)
  - Deaths         (self fainted)

Usage:
    python ps_replay_analyzer.py <replay_file.html|replay_file.txt>
"""

import sys
import re
from html.parser import HTMLParser
from collections import defaultdict

# Extract the raw log text from HTML or txt
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
# Have to manually set tags
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
}

# Sources that count as passive damage DEALT to the foe
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
    Parse HP strings like '195/280', '60/100', '0 fnt', '100/100'.
    Returns (current, max) as floats. max may be None if format is percentage.
    """
    hp_str = hp_str.strip()
    if "fnt" in hp_str:
        return 0, None
    if "/" in hp_str:
        parts = hp_str.split("/")
        return float(parts[0]), float(parts[1])
    # bare percentage or number
    return float(hp_str), None


def slot_to_name(slot_label: str) -> str:
    """
    'p1a: Gyarados' -> ('p1', 'Gyarados')
    'p2b: Scream Tail' -> ('p2', 'Scream Tail')
    """
    m = re.match(r"(p[12])[ab]:\s*(.+)", slot_label)
    if m:
        return m.group(1), m.group(2)
    return None, slot_label


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
    active = {}          # 'p1' / 'p2' -> pokemon name currently in slot a
    # Track current HP for each pokemon name (as percentage 0-100)
    hp = defaultdict(lambda: 100.0)   # pokemon_name -> current HP %
    max_hp = {}          # pokemon_name -> max HP (raw, if available)

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
    last_move_user = None   # (player, pokemon_name) that last used a move
    last_damage_target = None  # (player, pokemon_name) that was last damaged

    def record_damage(target_player, target_mon, amount, is_passive):
        """Record damage and attribute dealt-damage to the *other* side's active mon."""
        opponent = "p2" if target_player == "p1" else "p1"

        # Damage taken by target
        if is_passive:
            stats[target_player][target_mon]["passive_taken"] += amount
        else:
            stats[target_player][target_mon]["direct_taken"] += amount

        # Attribute dealt to whoever caused it
        if is_passive:
            # Passive damage dealt: attribute to active Pokemon of the opponent
            dealer_mon = active.get(opponent)
            if dealer_mon:
                stats[opponent][dealer_mon]["passive_dealt"] += amount
        else:
            # Direct damage: attribute to the last move user
            if last_move_user and last_move_user[0] == opponent:
                dealer_mon = last_move_user[1]
                stats[opponent][dealer_mon]["direct_dealt"] += amount
            else:
                # Fall back to active pokemon of opponent
                dealer_mon = active.get(opponent)
                if dealer_mon:
                    stats[opponent][dealer_mon]["direct_dealt"] += amount

    for line in lines:
        line = line.rstrip()
        if not line.startswith("|"):
            continue
        parts = line.split("|")
        # parts[0] is empty string before first |
        if len(parts) < 2:
            continue
        cmd = parts[1]

        # ── player identification ──────────────────
        if cmd == "player" and len(parts) >= 4:
            slot = parts[2]   # 'p1' or 'p2'
            name = parts[3]
            players[slot] = name

        # ── switch / drag (update active slot) ────
        elif cmd in ("switch", "drag", "replace") and len(parts) >= 4:
            slot_label = parts[2]    # e.g. 'p1a: Gyarados'
            mon_info   = parts[3]    # e.g. 'Gyarados, L79, F'
            hp_str     = parts[4] if len(parts) > 4 else "100/100"

            player, mon = slot_to_name(slot_label)
            if player:
                active[player] = mon

            # Record initial HP on switch-in
            cur, mx = parse_hp(hp_str)
            if mx:
                max_hp[mon] = mx
                hp[mon] = (cur / mx) * 100
            else:
                hp[mon] = cur  # already percentage

            # Ensure this pokemon appears in stats
            _ = stats[player][mon]

        # ── move (track last move user) ───────────
        elif cmd == "move" and len(parts) >= 3:
            slot_label = parts[2]
            player, mon = slot_to_name(slot_label)
            if player:
                last_move_user = (player, mon)

        # ── damage ────────────────────────────────
        elif cmd == "-damage" and len(parts) >= 4:
            slot_label = parts[2]
            hp_str     = parts[3]
            from_tag   = parts[4] if len(parts) > 4 else ""

            player, mon = slot_to_name(slot_label)
            if not player:
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

            record_damage(player, mon, damage_pct, is_passive)
            last_damage_target = (player, mon)

        # ── faint ─────────────────────────────────
        elif cmd == "faint" and len(parts) >= 3:
            slot_label = parts[2]
            player, mon = slot_to_name(slot_label)
            if not player:
                continue

            stats[player][mon]["deaths"] += 1

            # The killer is the active pokemon of the opponent
            opponent = "p2" if player == "p1" else "p1"
            killer = active.get(opponent)
            if killer:
                stats[opponent][killer]["kills"] += 1

            hp[mon] = 0

        # ── winner ────────────────────────────────
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

    # ── Header message ────────────────────────────────────────────────────
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


# MAIN RUN DIRECTLY FROM CMD
# Use: python replay_analyzer.py (html/txt file)
# Note that the file needs to be dir'd into or same dir
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