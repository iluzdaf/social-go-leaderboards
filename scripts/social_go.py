#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = OUTPUT_DIR / "site"
GAMES_JSON = DATA_DIR / "games.json"
LEADERBOARD_JSON = DATA_DIR / "leaderboard.json"
METADATA_CSV = DATA_DIR / "game_metadata.csv"
SETTINGS_JSON = DATA_DIR / "settings.json"
FIXTURES_DIR = ROOT / "fixtures"
SYNTHETIC_GAMES_DIR = FIXTURES_DIR / "synthetic-games"
SYNTHETIC_METADATA_CSV = FIXTURES_DIR / "synthetic-game_metadata.csv"

SUPPORTED_BOARD_SIZES = {9, 13, 19}
SUPPORTED_GAME_TYPES = {"standard", "pair-go"}
MARATHON_MIN_MOVES = 100
EDITION_AWARDS = [
    {
        "key": "marathon",
        "name": "🏃 Marathon",
        "points": 10,
        "source": "sgf",
        "pending": f"Pending: no game has reached {MARATHON_MIN_MOVES} moves",
    },
    {
        "key": "iceberg",
        "name": "🧊 Iceberg",
        "points": 20,
        "source": "katago",
        "pending": "Pending KataGo analysis",
    },
    {
        "key": "zen_master",
        "name": "🧘 Zen Master",
        "points": 20,
        "source": "katago",
        "pending": "Pending KataGo analysis",
    },
    {
        "key": "rollercoaster",
        "name": "🎢 Rollercoaster",
        "points": 20,
        "source": "katago",
        "pending": "Pending KataGo analysis",
    },
    {
        "key": "photo_finish",
        "name": "📸 Photo Finish",
        "points": 20,
        "source": "katago",
        "pending": "Pending KataGo analysis",
    },
]


@dataclass
class GameRecord:
    game_id: str
    session_date: str
    session_label: str
    filename: str
    path: str
    sgf_hash: str
    black: str
    white: str
    board_size: int
    komi: float | None
    result: str
    move_count: int
    game_type: str


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sgf_value(text: str, prop: str) -> str | None:
    match = re.search(rf"{re.escape(prop)}\[((?:\\.|[^\]])*)\]", text)
    if not match:
        return None
    return match.group(1).replace(r"\]", "]").replace(r"\\", "\\").strip()


def parse_int(value: str | None, default: int) -> int:
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def parse_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except ValueError:
        return None


def load_metadata(path: Path = METADATA_CSV) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    with path.open(newline="", encoding="utf-8") as f:
        return {
            row["filename"]: row
            for row in csv.DictReader(f)
            if row.get("filename")
        }


def load_settings() -> dict[str, Any]:
    settings = load_json(SETTINGS_JSON, {})
    status = settings.get("edition_status", "in_progress")
    if status not in {"in_progress", "final"}:
        status = "in_progress"
    return {"edition_status": status}


def parse_sgf(path: Path, session_date: str, session_label: str, metadata: dict[str, dict[str, str]]) -> GameRecord:
    text = path.read_text(encoding="utf-8", errors="replace")
    sgf_hash = file_hash(path)
    filename = path.name
    board_size = parse_int(sgf_value(text, "SZ"), 19)
    move_count = len(re.findall(r";[BW]\[[a-zA-Z]{0,2}\]", text))
    game_type = metadata.get(filename, {}).get("game_type", "standard").strip() or "standard"

    return GameRecord(
        game_id=sgf_hash[:12],
        session_date=session_date,
        session_label=session_label,
        filename=filename,
        path=str(path.relative_to(ROOT)),
        sgf_hash=sgf_hash,
        black=sgf_value(text, "PB") or "Unknown Black",
        white=sgf_value(text, "PW") or "Unknown White",
        board_size=board_size,
        komi=parse_float(sgf_value(text, "KM")),
        result=sgf_value(text, "RE") or "",
        move_count=move_count,
        game_type=game_type,
    )


def process_session(args: argparse.Namespace) -> None:
    session_dir = Path(args.session_dir)
    if not session_dir.is_absolute():
        session_dir = ROOT / session_dir
    if not session_dir.exists():
        raise SystemExit(f"Session folder does not exist: {session_dir}")

    session_date = args.date or session_dir.name
    session_label = args.label or session_date
    existing = load_json(GAMES_JSON, [])
    by_hash = {game["sgf_hash"]: game for game in existing}
    metadata = load_metadata()
    added = 0
    changed = 0

    for sgf_path in sorted(session_dir.glob("*.sgf")):
        record = parse_sgf(sgf_path, session_date, session_label, metadata)
        if record.sgf_hash in by_hash:
            continue
        old_same_path = next((g for g in existing if g["path"] == record.path), None)
        if old_same_path:
            existing.remove(old_same_path)
            changed += 1
        else:
            added += 1
        existing.append(asdict(record))

    existing.sort(key=lambda g: (g["session_date"], g["filename"]))
    write_json(GAMES_JSON, existing)
    leaderboard = build_leaderboard_data(existing)
    write_json(LEADERBOARD_JSON, leaderboard)
    print(f"Processed {added} new game(s), updated {changed} changed game(s).")
    print(f"Saved {GAMES_JSON.relative_to(ROOT)} and {LEADERBOARD_JSON.relative_to(ROOT)}.")


def game_players(game: dict[str, Any]) -> list[str]:
    players = []
    for side in (game["black"], game["white"]):
        players.extend(part.strip() for part in side.split("/") if part.strip())
    return players


def build_leaderboard_data(games: list[dict[str, Any]]) -> dict[str, Any]:
    players: dict[str, dict[str, Any]] = {}

    def player_row(name: str) -> dict[str, Any]:
        if name not in players:
            players[name] = {
                "player": name,
                "sessions": set(),
                "board_sizes": set(),
                "game_types": set(),
                "games_played": 0,
                "attendance_points": 0,
                "board_size_points": 0,
                "format_points": 0,
                "achievement_points": 0,
                "award_points": 0,
                "awards": [],
                "achievements": [],
            }
        return players[name]

    for game in games:
        for name in game_players(game):
            row = player_row(name)
            row["sessions"].add(game["session_date"])
            row["games_played"] += 1
            if game["board_size"] in SUPPORTED_BOARD_SIZES:
                row["board_sizes"].add(game["board_size"])
            row["game_types"].add(game["game_type"])

    for row in players.values():
        row["attendance_points"] = len(row["sessions"]) * 10
        row["board_size_points"] = len(row["board_sizes"]) * 10
        row["format_points"] = 20 if "pair-go" in row["game_types"] else 0
        if SUPPORTED_BOARD_SIZES.issubset(row["board_sizes"]):
            row["achievement_points"] += 30
            row["achievements"].append("🧭 Board Nomad")
        if SUPPORTED_GAME_TYPES.issubset(row["game_types"]):
            row["achievement_points"] += 30
            row["achievements"].append("🗺️ Go Explorer")

    marathon = marathon_award(games)
    if marathon:
        for name in marathon["recipients"]:
            row = player_row(name)
            row["award_points"] += 10
            row["awards"].append("🏃 Marathon")

    rows = []
    for row in players.values():
        total = (
            row["attendance_points"]
            + row["board_size_points"]
            + row["format_points"]
            + row["achievement_points"]
            + row["award_points"]
        )
        rows.append(
            {
                "player": row["player"],
                "total_points": total,
                "games_played": row["games_played"],
                "sessions": sorted(row["sessions"]),
                "board_sizes": sorted(row["board_sizes"]),
                "game_types": sorted(row["game_types"]),
                "attendance_points": row["attendance_points"],
                "board_size_points": row["board_size_points"],
                "format_points": row["format_points"],
                "achievement_points": row["achievement_points"],
                "award_points": row["award_points"],
                "awards": sorted(row["awards"]),
                "achievements": sorted(row["achievements"]),
            }
        )

    rows.sort(key=lambda r: (-r["total_points"], -r["games_played"], r["player"].lower()))
    settings = load_settings()
    return {
        "generated_on": date.today().isoformat(),
        "edition_status": settings["edition_status"],
        "games_count": len(games),
        "players_count": len(rows),
        "leaderboard": rows,
        "games": games,
        "edition_awards": edition_awards(games),
    }


def edition_awards(games: list[dict[str, Any]]) -> list[dict[str, Any]]:
    awards = []
    marathon = marathon_award(games)
    for award in EDITION_AWARDS:
        resolved = marathon if award["key"] == "marathon" else None
        awards.append(
            {
                "name": award["name"],
                "points": award["points"],
                "source": award["source"],
                "status": "current" if resolved else "pending",
                "detail": resolved["detail"] if resolved else award["pending"],
                "recipients": resolved["recipients"] if resolved else [],
                "target_game_id": resolved["target_game_id"] if resolved else "",
            }
        )
    return awards


def marathon_award(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    qualifying_games = [game for game in games if game["move_count"] >= MARATHON_MIN_MOVES]
    marathon = max(qualifying_games, key=lambda g: g["move_count"], default=None)
    if not marathon:
        return None
    return {
        "detail": f"{marathon['black']} vs {marathon['white']} / {marathon['move_count']} moves",
        "recipients": game_players(marathon),
        "target_game_id": marathon["game_id"],
    }


def build_site(_: argparse.Namespace) -> None:
    games = load_json(GAMES_JSON, [])
    data = build_leaderboard_data(games)
    write_json(LEADERBOARD_JSON, data)
    write_site(data)


def build_sample_site(_: argparse.Namespace) -> None:
    games = load_synthetic_games()
    data = build_leaderboard_data(games)
    write_site(data)


def write_site(data: dict[str, Any]) -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    copy_site_games(data["games"])
    (SITE_DIR / "index.html").write_text(render_site(data), encoding="utf-8")
    print(f"Built {str((SITE_DIR / 'index.html').relative_to(ROOT))}.")


def load_synthetic_games() -> list[dict[str, Any]]:
    metadata = load_metadata(SYNTHETIC_METADATA_CSV)
    games = []
    for session_dir in sorted(SYNTHETIC_GAMES_DIR.iterdir()):
        if not session_dir.is_dir():
            continue
        session_date = session_dir.name
        session_label = synthetic_session_label(session_date)
        for sgf_path in sorted(session_dir.glob("*.sgf")):
            games.append(asdict(parse_sgf(sgf_path, session_date, session_label, metadata)))
    games.sort(key=lambda g: (g["session_date"], g["filename"]))
    return games


def synthetic_session_label(session_date: str) -> str:
    labels = {
        "2026-08-27": "27 Aug",
        "2026-09-03": "3 Sep",
    }
    return labels.get(session_date, session_date)


def copy_site_games(games: list[dict[str, Any]]) -> None:
    for copied_dir in (SITE_DIR / "games", SITE_DIR / "fixtures"):
        if copied_dir.exists():
            shutil.rmtree(copied_dir)
    for game in games:
        source = ROOT / game["path"]
        if not source.exists():
            continue
        target = SITE_DIR / game["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)


def render_site(data: dict[str, Any]) -> str:
    rows = data["leaderboard"]
    games = data["games"]
    awards = data["edition_awards"]
    awards_title = "Awards"
    leader_rows = "\n".join(render_leader_row(i + 1, row, data["edition_status"]) for i, row in enumerate(rows))
    game_rows = "\n".join(render_game_row(game) for game in games)
    award_cards = "\n".join(render_award_card(award, data["edition_status"]) for award in awards)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Social Go at Kembangan</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f7f4ee;
      --ink: #1d2522;
      --muted: #65706b;
      --line: #ddd6c9;
      --panel: #fffdf8;
      --accent: #1f7a5d;
      --accent-2: #c85f32;
      --gold: #d7a947;
      --silver: #aeb4ba;
      --pending: #d78396;
      --disabled-text: #8f8880;
    }}
    * {{ box-sizing: border-box; }}
    html {{
      scroll-behavior: smooth;
    }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top left, #e7f2eb 0, transparent 30rem), var(--bg);
      color: var(--ink);
    }}
    main {{
      width: min(1120px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 56px;
    }}
    header {{
      display: grid;
      grid-template-columns: 1fr auto;
      gap: 24px;
      align-items: end;
      padding: 28px 0 24px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2.2rem, 6vw, 5.5rem);
      line-height: .92;
      letter-spacing: 0;
      max-width: 780px;
    }}
    .meta {{
      color: var(--muted);
      font-size: .95rem;
      line-height: 1.5;
      text-align: right;
    }}
    .metric, .panel {{
      background: color-mix(in srgb, var(--panel) 92%, white);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 50px rgba(43, 35, 22, .08);
    }}
    .metric span, th {{
      color: var(--muted);
      font-size: .78rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    .grid {{
      display: grid;
      grid-template-columns: 1.5fr .9fr;
      gap: 18px;
      align-items: start;
    }}
    .panel {{
      overflow: hidden;
    }}
    .leaderboard-panel {{
      overflow: visible;
    }}
    .panel h2 {{
      margin: 0;
      padding: 18px 20px;
      font-size: 1.1rem;
      border-bottom: 1px solid var(--line);
    }}
    .collapsible {{
      margin-top: 18px;
    }}
    .collapsible:first-of-type {{
      margin-top: 28px;
    }}
    .collapsible > summary {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      min-height: 58px;
      padding: 0 20px;
      cursor: pointer;
      border-bottom: 1px solid var(--line);
      font-size: 1.1rem;
      font-weight: 800;
      list-style: none;
    }}
    .collapsible > summary::-webkit-details-marker {{
      display: none;
    }}
    .collapsible > summary::after {{
      content: "+";
      color: var(--muted);
      font-size: 1.25rem;
      line-height: 1;
    }}
    .collapsible[open] > summary::after {{
      content: "-";
    }}
    .section-body {{
      padding: 16px 20px 18px;
    }}
    .leaderboard-section {{
      overflow: visible;
    }}
    .leaderboard-content {{
      display: grid;
      grid-template-columns: 1.5fr .9fr;
      gap: 22px;
      align-items: start;
    }}
    .leaderboard-column {{
      min-width: 0;
    }}
    .leaderboard-column h3 {{
      margin: 0 0 12px;
      font-size: 1rem;
    }}
    .section-meta {{
      color: var(--muted);
      font-size: .92rem;
      line-height: 1.45;
      margin-top: 14px;
    }}
    .leaderboard-meta {{
      grid-column: 1 / -1;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .rank {{
      width: 48px;
      color: var(--ink);
      font-weight: 800;
    }}
    .place-1 {{
      background: linear-gradient(90deg, rgba(215, 169, 71, .24), transparent 72%);
    }}
    .place-2 {{
      background: linear-gradient(90deg, rgba(174, 180, 186, .26), transparent 72%);
    }}
    .points {{
      font-weight: 800;
      font-size: 1.15rem;
      white-space: nowrap;
    }}
    .points-formula {{
      display: inline-flex;
      align-items: baseline;
      gap: 5px;
      white-space: nowrap;
    }}
    .points-confirmed {{
      color: var(--ink);
    }}
    .points-provisional {{
      color: var(--accent-2);
    }}
    .points-total {{
      color: var(--ink);
      font-weight: 900;
    }}
    .points-operator {{
      color: var(--muted);
      font-weight: 700;
    }}
    .points-heading {{
      text-align: center;
    }}
    .score-cell {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
    }}
    .breakdown {{
      position: relative;
    }}
    .breakdown summary {{
      display: inline-grid;
      place-items: center;
      width: 28px;
      height: 28px;
      border: 1px solid var(--line);
      border-radius: 999px;
      color: var(--accent);
      background: #fffdf8;
      cursor: pointer;
      font-size: .85rem;
      font-weight: 800;
      list-style: none;
    }}
    .breakdown summary::-webkit-details-marker {{
      display: none;
    }}
    .breakdown summary::marker {{
      content: "";
    }}
    .breakdown[open] summary {{
      border-color: var(--accent);
      box-shadow: 0 0 0 3px rgba(31, 122, 93, .12);
    }}
    .breakdown-panel {{
      position: absolute;
      right: 0;
      z-index: 5;
      width: min(320px, calc(100vw - 48px));
      margin-top: 8px;
      padding: 14px;
      border: 1px solid var(--line);
      border-radius: 8px;
      background: #fffdf8;
      box-shadow: 0 18px 50px rgba(43, 35, 22, .16);
      color: var(--ink);
      white-space: normal;
    }}
    .breakdown-sections {{
      display: grid;
      gap: 12px;
      margin: 0;
      padding: 0;
      list-style: none;
      font-size: .9rem;
    }}
    .breakdown-group {{
      display: grid;
      gap: 10px;
    }}
    .breakdown-group-title {{
      color: var(--accent);
      font-size: .75rem;
      font-weight: 900;
      letter-spacing: .08em;
      text-transform: uppercase;
    }}
    .breakdown-section {{
      display: grid;
      gap: 4px;
      padding-bottom: 10px;
      border-bottom: 1px solid var(--line);
    }}
    .breakdown-section:last-child {{
      padding-bottom: 0;
      border-bottom: 0;
    }}
    .breakdown-title {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-weight: 800;
    }}
    .breakdown-detail {{
      color: var(--muted);
      line-height: 1.35;
    }}
    .breakdown-points {{
      white-space: nowrap;
    }}
    .sub {{
      color: var(--muted);
      font-size: .88rem;
      margin-top: 3px;
    }}
    .game-link {{
      color: inherit;
      text-decoration: none;
    }}
    .game-link:hover {{
      color: var(--accent);
      text-decoration: underline;
      text-underline-offset: 3px;
    }}
    .game-row.is-highlighted td {{
      animation: game-highlight 2.8s ease-out;
    }}
    @keyframes game-highlight {{
      0% {{
        background: #e7f2eb;
        outline: 2px solid rgba(31, 122, 93, .28);
        outline-offset: -2px;
      }}
      70% {{
        background: #e7f2eb;
        outline: 2px solid rgba(31, 122, 93, .18);
        outline-offset: -2px;
      }}
      100% {{
        background: transparent;
        outline: 2px solid rgba(31, 122, 93, 0);
        outline-offset: -2px;
      }}
    }}
    .awards {{
      display: grid;
      gap: 12px;
      padding: 16px;
    }}
    .award {{
      display: grid;
      gap: 4px;
      border-left: 4px solid var(--accent);
      padding: 12px 12px 12px 14px;
      background: #e7f2eb;
      border-radius: 4px;
      color: inherit;
      text-decoration: none;
    }}
    .award[href]:hover {{
      border-left-color: #155c47;
      box-shadow: 0 10px 30px rgba(31, 122, 93, .12);
    }}
    .award-pending {{
      border-left-color: var(--pending);
      background: #fff1f5;
      color: var(--disabled-text);
    }}
    .award-pending .award-points,
    .award-pending .award-meta {{
      color: var(--disabled-text);
    }}
    .award-title {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-weight: 800;
    }}
    .award-points {{
      white-space: nowrap;
    }}
    .award-meta {{
      display: block;
      color: var(--muted);
      font-size: .86rem;
      line-height: 1.35;
    }}
    .rules-content {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 18px;
    }}
    .rules-group {{
      display: grid;
      gap: 10px;
      align-content: start;
    }}
    .rules-group h3 {{
      margin: 0;
      font-size: 1rem;
    }}
    .rules-group p {{
      margin: 0;
      color: var(--muted);
      line-height: 1.45;
    }}
    .rules-list {{
      display: grid;
      gap: 8px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .rules-list li {{
      display: grid;
      gap: 2px;
      padding-bottom: 8px;
      border-bottom: 1px solid var(--line);
    }}
    .rules-list li:last-child {{
      padding-bottom: 0;
      border-bottom: 0;
    }}
    .rules-title {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-weight: 800;
    }}
    .rules-detail {{
      color: var(--muted);
      line-height: 1.35;
      font-size: .9rem;
    }}
    .privacy-content {{
      color: var(--muted);
      line-height: 1.55;
    }}
    .privacy-content p {{
      margin: 0 0 10px;
    }}
    .privacy-content p:last-child {{
      margin-bottom: 0;
    }}
    @media (max-width: 760px) {{
      main {{ width: min(100% - 20px, 1120px); padding-top: 16px; }}
      header, .grid, .rules-content, .leaderboard-content {{ grid-template-columns: 1fr; }}
      .meta {{ text-align: left; }}
      .score-cell {{ justify-content: center; }}
    }}
  </style>
</head>
<body>
  <main>
    <header>
      <h1>Social Go at Kembangan</h1>
    </header>

    <details class="panel collapsible">
      <summary>How Points Work</summary>
      <div class="section-body rules-content">
        <div class="rules-group">
          <h3>Confirmed Points</h3>
          <p>These are locked once earned.</p>
          <ul class="rules-list">
            {rules_item("Attend a session", "10 pts", "Awarded once per session attended.")}
            {rules_item("Play 9x9", "10 pts", "Awarded once after your first 9x9 game.")}
            {rules_item("Play 13x13", "10 pts", "Awarded once after your first 13x13 game.")}
            {rules_item("Play 19x19", "10 pts", "Awarded once after your first 19x19 game.")}
            {rules_item("Play Pair Go", "20 pts", "Awarded once after your first Pair Go game.")}
            {rules_item("🧭 Board Nomad", "+30 pts", "Play all 3 board sizes.")}
            {rules_item("🗺️ Go Explorer", "+30 pts", "Play Standard and Pair Go.")}
            {rules_item("🐧 First Penguin", "20 pts", "First dramatic AI win-rate collapse.")}
          </ul>
        </div>
        <div class="rules-group">
          <h3>Provisional Awards</h3>
          <p>These may change until the edition is finalized.</p>
          <ul class="rules-list">
            {rules_item("🧊 Iceberg", "20 pts", "Biggest AI win-rate collapse.")}
            {rules_item("🧘 Zen Master", "20 pts", "Most accurate game.")}
            {rules_item("🎢 Rollercoaster", "20 pts", "Most AI win-rate swings.")}
            {rules_item("📸 Photo Finish", "20 pts", "Closest game.")}
            {rules_item("🏃 Marathon", "10 pts", f"Longest game of at least {MARATHON_MIN_MOVES} moves.")}
          </ul>
        </div>
      </div>
    </details>

    <details class="panel collapsible leaderboard-section" open>
      <summary>Leaderboard</summary>
      <div class="section-body leaderboard-content">
        <div class="leaderboard-column leaderboard-panel">
          <h3>Standings</h3>
          <table>
            <thead><tr><th>Rank</th><th>Player</th><th class="points-heading">Points</th></tr></thead>
            <tbody>{leader_rows or '<tr><td colspan="3">No games processed yet.</td></tr>'}</tbody>
          </table>
        </div>
        <aside class="leaderboard-column">
          <h3>{awards_title}</h3>
          <div class="awards">{award_cards}</div>
        </aside>
        <div class="section-meta leaderboard-meta">
          Updated {escape(data["generated_on"])}<br>
          {data["games_count"]} games across {len(session_labels(games))} sessions
        </div>
      </div>
    </details>

    <details class="panel collapsible games-section">
      <summary>Games</summary>
      <div class="section-body">
        <table>
          <thead><tr><th>Date</th><th>Game</th><th>Result</th></tr></thead>
          <tbody>{game_rows or '<tr><td colspan="3">No games processed yet.</td></tr>'}</tbody>
        </table>
      </div>
    </details>

    <details class="panel collapsible">
      <summary>Privacy</summary>
      <div class="section-body privacy-content">
        <p>Games submitted for the leaderboard may be published on this site, and SGF files may be downloadable.</p>
        <p>Please avoid including personal information in SGF comments, filenames, player names, or metadata. Use your preferred display name if you do not want your full name shown.</p>
        <p>If you want a game corrected, renamed, or removed, contact the organiser.</p>
      </div>
    </details>
  </main>
  <script>
    document.querySelectorAll(".breakdown").forEach((current) => {{
      current.addEventListener("toggle", () => {{
        if (!current.open) return;
        document.querySelectorAll(".breakdown[open]").forEach((other) => {{
          if (other !== current) other.open = false;
        }});
      }});
    }});
    document.addEventListener("click", (event) => {{
      if (event.target.closest(".breakdown")) return;
      document.querySelectorAll(".breakdown[open]").forEach((openBreakdown) => {{
        openBreakdown.open = false;
      }});
    }});
    function highlightLinkedGame() {{
      if (!window.location.hash.startsWith("#game-")) return;
      const gamesSection = document.querySelector(".games-section");
      if (gamesSection) gamesSection.open = true;
      const row = document.querySelector(window.location.hash);
      if (!row) return;
      row.scrollIntoView({{ behavior: "smooth", block: "center" }});
      row.classList.remove("is-highlighted");
      void row.offsetWidth;
      row.classList.add("is-highlighted");
      window.setTimeout(() => {{
        row.classList.remove("is-highlighted");
      }}, 2800);
    }}
    document.querySelectorAll(".award[href^='#game-']").forEach((awardLink) => {{
      awardLink.addEventListener("click", () => {{
        window.setTimeout(highlightLinkedGame, 0);
      }});
    }});
    window.addEventListener("hashchange", highlightLinkedGame);
    highlightLinkedGame();
  </script>
</body>
</html>
"""


def render_leader_row(rank: int, row: dict[str, Any], edition_status: str) -> str:
    class_name = f' class="place-{rank}"' if rank in {1, 2} else ""
    return f"""<tr{class_name}>
  <td class="rank">{rank}</td>
  <td><strong>{escape(row["player"])}</strong></td>
  <td>
    <div class="score-cell">
      {render_score(row)}
      {render_breakdown(row, edition_status)}
    </div>
  </td>
</tr>"""


def render_score(row: dict[str, Any]) -> str:
    provisional = row["award_points"]
    confirmed = row["total_points"] - provisional
    if provisional == 0:
        return f"""<span class="points">{row["total_points"]}</span>"""
    return f"""<span class="points points-formula">
  <span class="points-confirmed">{confirmed}</span>
  <span class="points-operator">+</span>
  <span class="points-provisional">{provisional}</span>
  <span class="points-operator">=</span>
  <span class="points-total">{row["total_points"]}</span>
</span>"""


def rules_item(title: str, points: str, detail: str) -> str:
    return f"""<li>
  <div class="rules-title">
    <span>{escape(title)}</span>
    <strong>{escape(points)}</strong>
  </div>
  <div class="rules-detail">{escape(detail)}</div>
</li>"""


def render_breakdown(row: dict[str, Any], edition_status: str) -> str:
    boards = len(row["board_sizes"])
    sessions = len(row["sessions"])
    formats = []
    if "pair-go" in row["game_types"]:
        formats.append("Pair Go")
    awards_title = "Final Awards" if edition_status == "final" else "Provisional Awards"
    awards = render_awards_breakdown(row["awards"], edition_status)
    milestones = ", ".join(row["achievements"]) if row["achievements"] else "No milestones yet"
    board_sizes = ", ".join(f"{size}x{size}" for size in row["board_sizes"]) or "No board sizes yet"
    session_list = ", ".join(row["sessions"]) or "No sessions yet"
    format_list = ", ".join(formats) if formats else "No formats yet"
    return f"""<details class="breakdown">
  <summary aria-label="Show points breakdown for {escape(row["player"])}">?</summary>
  <div class="breakdown-panel">
    <div class="breakdown-sections">
      <div class="breakdown-group">
        <div class="breakdown-group-title">Confirmed Points</div>
        {breakdown_section("Sessions", escape(f"{sessions} session(s): {session_list}"), row["attendance_points"])}
        {breakdown_section("Board sizes", escape(f"{boards} board size(s): {board_sizes}"), row["board_size_points"])}
        {breakdown_section("Formats", escape(format_list), row["format_points"])}
        {breakdown_section("Milestones", escape(milestones), row["achievement_points"])}
      </div>
      <div class="breakdown-group">
        <div class="breakdown-group-title">{awards_title}</div>
        {breakdown_section("Awards", awards, row["award_points"], provisional=edition_status != "final" and row["award_points"] > 0)}
      </div>
    </div>
  </div>
</details>"""


def render_awards_breakdown(awards: list[str], edition_status: str) -> str:
    if not awards:
        award_label = "final" if edition_status == "final" else "provisional"
        return escape(f"No {award_label} awards")
    if edition_status == "final":
        return escape(", ".join(awards))
    return escape(", ".join(awards))


def breakdown_section(title: str, detail: str, points: int, provisional: bool = False) -> str:
    points_class = "breakdown-points points-provisional" if provisional else "breakdown-points"
    return f"""<section class="breakdown-section">
  <div class="breakdown-title">
    <span>{escape(title)}</span>
    <strong class="{points_class}">{points} pts</strong>
  </div>
  <div class="breakdown-detail">{detail}</div>
</section>"""


def render_game_row(game: dict[str, Any]) -> str:
    metadata = f"{game['board_size']}x{game['board_size']} / {game['move_count']} moves"
    if game["game_type"] != "standard":
        metadata = f"{metadata} / {game['game_type']}"
    sgf_href = escape(game["path"])
    game_title = f"{escape(game['black'])} vs {escape(game['white'])}"
    return f"""<tr class="game-row" id="game-{escape(game["game_id"])}">
  <td>{escape(game["session_label"])}</td>
  <td><a class="game-link" href="{sgf_href}" download="{escape(game["filename"])}"><strong>{game_title}</strong></a><div class="sub">{escape(metadata)}</div></td>
  <td>{escape(game["result"])}</td>
</tr>"""


def render_award_card(award: dict[str, Any], edition_status: str) -> str:
    class_name = "award award-pending" if award["status"] == "pending" else "award"
    status_label = "Final" if edition_status == "final" else "Currently"
    if award["status"] == "pending":
        status_label = "Pending"
    points_class = "award-points"
    if award["status"] != "pending" and edition_status != "final":
        points_class = "award-points points-provisional"
    recipients = ", ".join(award["recipients"])
    detail = f"{status_label}: {recipients}" if recipients else award["detail"]
    tag = "a" if award.get("target_game_id") else "div"
    href = f' href="#game-{escape(award["target_game_id"])}"' if award.get("target_game_id") else ""
    return f"""<{tag} class="{class_name}"{href}>
  <div class="award-title">
    <span>{escape(award["name"])}</span>
    <strong class="{points_class}">+{award["points"]} pts</strong>
  </div>
  <span class="award-meta">{escape(detail)}</span>
</{tag}>"""


def session_labels(games: list[dict[str, Any]]) -> list[str]:
    return sorted({game["session_label"] for game in games})


def escape(value: Any) -> str:
    return html.escape(str(value), quote=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Social Go leaderboard tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    process = subparsers.add_parser("process", help="Process a folder of SGF files")
    process.add_argument("session_dir", help="Folder containing SGF files")
    process.add_argument("--date", help="Session date, for example 2026-08-27")
    process.add_argument("--label", help="Display label, for example '27 Aug'")
    process.set_defaults(func=process_session)

    build = subparsers.add_parser("build-site", help="Build the static website")
    build.set_defaults(func=build_site)

    sample = subparsers.add_parser("build-sample-site", help="Build the static website with synthetic fixture data")
    sample.set_defaults(func=build_sample_site)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
