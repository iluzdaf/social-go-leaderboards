#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import shutil
import subprocess
from dataclasses import dataclass, asdict
from datetime import date
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"
SITE_DIR = OUTPUT_DIR / "site"
ASSETS_DIR = ROOT / "assets"
GAMES_JSON = DATA_DIR / "games.json"
LEADERBOARD_JSON = DATA_DIR / "leaderboard.json"
METADATA_CSV = DATA_DIR / "game_metadata.csv"
SETTINGS_JSON = DATA_DIR / "settings.json"
ANALYSIS_DIR = ROOT / "analysis"
FIXTURES_DIR = ROOT / "fixtures"
SYNTHETIC_GAMES_DIR = FIXTURES_DIR / "synthetic-games"
SYNTHETIC_ANALYSIS_DIR = FIXTURES_DIR / "synthetic-analysis"
SYNTHETIC_METADATA_CSV = FIXTURES_DIR / "synthetic-game_metadata.csv"
DEFAULT_KATAGO_CONFIG = Path("/opt/homebrew/Cellar/katago/1.16.4/share/katago/configs/analysis_example.cfg")
DEFAULT_KATAGO_MODEL = Path("/opt/homebrew/Cellar/katago/1.16.4/share/katago/g170e-b20c256x2-s5303129600-d1228401921.bin.gz")

SUPPORTED_BOARD_SIZES = {9, 13, 19}
SUPPORTED_GAME_TYPES = {"standard", "pair-go"}
MARATHON_MIN_MOVES = 100
FIRST_PENGUIN_MIN_DROP = 40.0
FIRST_PENGUIN_POINTS = 20
ROLLERCOASTER_MIN_SWING = 20.0
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
        "pending": "Pending analysis",
    },
    {
        "key": "zen_master",
        "name": "🧘 Zen Master",
        "points": 20,
        "source": "katago",
        "pending": "Pending analysis",
    },
    {
        "key": "rollercoaster",
        "name": "🎢 Rollercoaster",
        "points": 20,
        "source": "katago",
        "pending": "Pending analysis",
    },
    # Photo Finish needs point-loss analysis from KataGo, so it is disabled for now.
    # {
    #     "key": "photo_finish",
    #     "name": "📸 Photo Finish",
    #     "points": 20,
    #     "source": "katago",
    #     "pending": "Pending analysis",
    # },
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


def parse_sgf_moves(path: Path, board_size: int) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    moves = []
    for move_number, match in enumerate(re.finditer(r";([BW])\[([a-zA-Z]{0,2})\]", text), start=1):
        color = match.group(1)
        sgf_point = match.group(2).lower()
        vertex = sgf_point_to_vertex(sgf_point, board_size)
        moves.append(
            {
                "moveNumber": move_number,
                "color": color,
                "point": sgf_point_to_gtp(sgf_point, board_size),
                "pass": vertex is None,
                "x": vertex[0] if vertex else None,
                "y": vertex[1] if vertex else None,
            }
        )
    return moves


def sgf_point_to_gtp(point: str, board_size: int) -> str:
    vertex = sgf_point_to_vertex(point, board_size)
    if vertex is None:
        return "pass"
    x, y = vertex
    columns = "ABCDEFGHJKLMNOPQRST"
    return f"{columns[x]}{board_size - y}"


def sgf_point_to_vertex(point: str, board_size: int) -> tuple[int, int] | None:
    if point == "" or point.lower() == "tt":
        return None
    if len(point) != 2:
        raise ValueError(f"Unsupported SGF point: {point}")
    x = ord(point[0]) - ord("a")
    y = ord(point[1]) - ord("a")
    if x < 0 or y < 0 or x >= board_size or y >= board_size:
        raise ValueError(f"SGF point {point} is outside a {board_size}x{board_size} board")
    return (x, y)


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


def analyze_session_with_katago(args: argparse.Namespace) -> None:
    session_dir = Path(args.session_dir)
    if not session_dir.is_absolute():
        session_dir = ROOT / session_dir
    if not session_dir.exists():
        raise SystemExit(f"Session folder does not exist: {session_dir}")
    metadata = load_metadata(resolve_path(args.metadata))
    games = [
        asdict(parse_sgf(sgf_path, args.date or session_dir.name, args.label or session_dir.name, metadata))
        for sgf_path in sorted(session_dir.glob("*.sgf"))
    ]
    analyze_games_with_katago(games, resolve_path(args.analysis_dir), args)


def analyze_sample_with_katago(args: argparse.Namespace) -> None:
    games = load_synthetic_games()
    analyze_games_with_katago(games, SYNTHETIC_ANALYSIS_DIR, args)


def analyze_games_with_katago(games: list[dict[str, Any]], analysis_dir: Path, args: argparse.Namespace) -> None:
    katago = resolve_executable(args.katago)
    config = resolve_path(args.config)
    model = resolve_path(args.model)
    if not config.exists():
        raise SystemExit(f"KataGo config does not exist: {config}")
    if not model.exists():
        raise SystemExit(f"KataGo model does not exist: {model}")
    if not games:
        print("No SGF files found.")
        return

    engine = KataGoAnalysisEngine(katago, config, model, args.visits)
    analyzed = 0
    try:
        for game in games:
            output_path = analysis_dir / f"{Path(game['filename']).stem}.json"
            analysis = analyze_game_with_engine(game, engine, args.rules)
            write_json(output_path, analysis)
            analyzed += 1
            summary = analysis.get("summary", {}).get("biggest_winrate_loss")
            if summary:
                print(
                    f"Analyzed {game['filename']}: biggest drop "
                    f"{summary['player']} move {summary['move_number']} "
                    f"({summary['winrate_drop']} pts)."
                )
            else:
                print(f"Analyzed {game['filename']}: no drops found.")
    finally:
        engine.close()
    print(f"Saved KataGo analysis for {analyzed} game(s) in {analysis_dir.relative_to(ROOT)}.")


class KataGoAnalysisEngine:
    def __init__(self, katago: str, config: Path, model: Path, visits: int):
        self.visits = visits
        overrides = ",".join(
            [
                f"maxVisits={visits}",
                "numAnalysisThreads=1",
                "numSearchThreadsPerAnalysisThread=1",
                "logToStderr=false",
                "reportAnalysisWinratesAs=BLACK",
            ]
        )
        self.proc = subprocess.Popen(
            [
                katago,
                "analysis",
                "-config",
                str(config),
                "-model",
                str(model),
                "-override-config",
                overrides,
                "-quit-without-waiting",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=ROOT,
        )

    def query(self, request: dict[str, Any], expected_responses: int) -> list[dict[str, Any]]:
        if not self.proc.stdin or not self.proc.stdout:
            raise RuntimeError("KataGo process is not available")
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        responses = []
        while len(responses) < expected_responses:
            line = self.proc.stdout.readline()
            if line == "":
                raise RuntimeError("KataGo exited before returning all analysis responses")
            response = json.loads(line)
            if response.get("id") != request["id"]:
                continue
            if "error" in response:
                raise RuntimeError(f"KataGo analysis error: {response['error']}")
            if "warning" in response:
                print(f"KataGo warning for {request['id']}: {response['warning']}")
                continue
            if response.get("isDuringSearch"):
                continue
            responses.append(response)
        return responses

    def close(self) -> None:
        if self.proc.stdin:
            self.proc.stdin.close()
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()


def analyze_game_with_engine(game: dict[str, Any], engine: KataGoAnalysisEngine, rules: str) -> dict[str, Any]:
    moves = parse_sgf_moves(ROOT / game["path"], game["board_size"])
    analyze_turns = list(range(len(moves) + 1))
    request = {
        "id": game["game_id"],
        "moves": [[move["color"], move["point"]] for move in moves],
        "rules": rules,
        "komi": game["komi"] if game["komi"] is not None else 6.5,
        "boardXSize": game["board_size"],
        "boardYSize": game["board_size"],
        "analyzeTurns": analyze_turns,
    }
    responses = engine.query(request, len(analyze_turns))
    winrates_by_turn = {
        int(response["turnNumber"]): float(response.get("rootInfo", {}).get("winrate", 0.0))
        for response in responses
    }
    events = winrate_drop_events(game, moves, winrates_by_turn)
    biggest_loss = max(events, key=lambda event: event["winrate_drop"], default=None)
    return {
        "game_id": game["game_id"],
        "filename": game["filename"],
        "generated_by": "katago",
        "katago": {
            "visits": engine_visits(engine),
            "winrate_perspective": "black",
        },
        "summary": {
            "biggest_winrate_loss": biggest_loss,
        },
        "events": events,
    }


def engine_visits(engine: KataGoAnalysisEngine) -> int:
    return engine.visits


def winrate_drop_events(
    game: dict[str, Any],
    moves: list[dict[str, Any]],
    winrates_by_turn: dict[int, float],
) -> list[dict[str, Any]]:
    events = []
    for turn, move in enumerate(moves, start=1):
        if turn - 1 not in winrates_by_turn or turn not in winrates_by_turn:
            continue
        color = move["color"]
        before = player_winrate(color, winrates_by_turn[turn - 1])
        after = player_winrate(color, winrates_by_turn[turn])
        drop = max(0.0, before - after)
        events.append(
            {
                "move_number": turn,
                "color": color,
                "player": side_name_for_color(game, color),
                "move": move["point"],
                "winrate_before": round(before * 100, 1),
                "winrate_after": round(after * 100, 1),
                "winrate_drop": round(drop * 100, 1),
            }
        )
    return sorted(events, key=lambda event: event["winrate_drop"], reverse=True)


def player_winrate(color: str, black_winrate: float) -> float:
    return black_winrate if color == "B" else 1.0 - black_winrate


def side_name_for_color(game: dict[str, Any], color: str) -> str:
    return game["black"] if color == "B" else game["white"]


def resolve_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def resolve_executable(value: str) -> str:
    if "/" in value:
        return value
    resolved = shutil.which(value)
    if not resolved:
        raise SystemExit(f"Could not find executable: {value}")
    return resolved


def game_players(game: dict[str, Any]) -> list[str]:
    players = []
    for side in (game["black"], game["white"]):
        players.extend(part.strip() for part in side.split("/") if part.strip())
    return players


def build_leaderboard_data(games: list[dict[str, Any]], analysis_dir: Path = ANALYSIS_DIR) -> dict[str, Any]:
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

    first_penguin = first_penguin_award(games, analysis_dir)
    if first_penguin:
        row = player_row(first_penguin["recipient"])
        row["achievement_points"] += FIRST_PENGUIN_POINTS
        row["achievements"].append("🐧 First Penguin")

    edition_award_rows = edition_awards(games, analysis_dir)
    for award in edition_award_rows:
        if award["status"] != "current":
            continue
        for name in award["recipients"]:
            row = player_row(name)
            row["award_points"] += award["points"]
            row["awards"].append(award["name"])

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
        "edition_awards": edition_award_rows,
        "first_penguin": first_penguin,
    }


def edition_awards(games: list[dict[str, Any]], analysis_dir: Path = ANALYSIS_DIR) -> list[dict[str, Any]]:
    awards = []
    resolved_awards = {
        "marathon": marathon_award(games),
        "iceberg": iceberg_award(games, analysis_dir),
        "zen_master": zen_master_award(games, analysis_dir),
        "rollercoaster": rollercoaster_award(games, analysis_dir),
    }
    for award in EDITION_AWARDS:
        resolved = resolved_awards.get(award["key"])
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


def first_penguin_award(games: list[dict[str, Any]], analysis_dir: Path) -> dict[str, Any] | None:
    candidates = []
    game_order = {game["game_id"]: index for index, game in enumerate(games)}
    for game in games:
        for event in analysis_events_for_game(game, analysis_dir):
            drop = winrate_drop_points(event)
            if drop < FIRST_PENGUIN_MIN_DROP:
                continue
            recipient = str(event.get("player", "")).strip()
            if not recipient:
                continue
            candidates.append(
                {
                    "recipient": recipient,
                    "game_id": game["game_id"],
                    "game_label": f"{game['black']} vs {game['white']}",
                    "move_number": int(event.get("move_number", 0) or 0),
                    "winrate_drop": round(drop, 1),
                    "sort_key": (game["session_date"], game_order[game["game_id"]], int(event.get("move_number", 0) or 0)),
                }
            )
    if not candidates:
        return None
    winner = min(candidates, key=lambda candidate: candidate["sort_key"])
    winner.pop("sort_key", None)
    return winner


def iceberg_award(games: list[dict[str, Any]], analysis_dir: Path) -> dict[str, Any] | None:
    candidates = []
    game_order = {game["game_id"]: index for index, game in enumerate(games)}
    for game in games:
        for event in analysis_events_for_game(game, analysis_dir):
            drop = winrate_drop_points(event)
            recipient = str(event.get("player", "")).strip()
            if not recipient:
                continue
            candidates.append(
                {
                    "detail": f"{round(drop, 1)} point drop",
                    "recipients": award_recipients(recipient),
                    "target_game_id": game["game_id"],
                    "move_number": int(event.get("move_number", 0) or 0),
                    "winrate_drop": round(drop, 1),
                    "sort_key": (drop, -game_order[game["game_id"]], -int(event.get("move_number", 0) or 0)),
                }
            )
    if not candidates:
        return None
    winner = max(candidates, key=lambda candidate: candidate["sort_key"])
    winner.pop("sort_key", None)
    return winner


def zen_master_award(games: list[dict[str, Any]], analysis_dir: Path) -> dict[str, Any] | None:
    candidates = []
    game_order = {game["game_id"]: index for index, game in enumerate(games)}
    for game in games:
        events = analysis_events_for_game(game, analysis_dir)
        sides: dict[str, dict[str, float]] = {}
        for event in events:
            side = str(event.get("player", "")).strip()
            if not side:
                continue
            row = sides.setdefault(side, {"loss_total": 0.0, "moves_analyzed": 0})
            row["loss_total"] += winrate_drop_points(event)
            row["moves_analyzed"] += 1
        for side, row in sides.items():
            if not row["moves_analyzed"]:
                continue
            average_loss = row["loss_total"] / row["moves_analyzed"]
            candidates.append(
                {
                    "detail": f"{round(average_loss, 1)} avg loss",
                    "recipients": award_recipients(side),
                    "target_game_id": game["game_id"],
                    "average_loss": round(average_loss, 1),
                    "moves_analyzed": row["moves_analyzed"],
                    "sort_key": (average_loss, -row["moves_analyzed"], game_order[game["game_id"]], side.lower()),
                }
            )
    if not candidates:
        return None
    winner = min(candidates, key=lambda candidate: candidate["sort_key"])
    winner.pop("sort_key", None)
    return winner


def rollercoaster_award(games: list[dict[str, Any]], analysis_dir: Path) -> dict[str, Any] | None:
    candidates = []
    game_order = {game["game_id"]: index for index, game in enumerate(games)}
    for game in games:
        swings = []
        for event in analysis_events_for_game(game, analysis_dir):
            drop = winrate_drop_points(event)
            if drop >= ROLLERCOASTER_MIN_SWING:
                swings.append(drop)
        if not swings:
            continue
        swing_count = len(swings)
        swing_total = sum(swings)
        swing_label = "swing" if swing_count == 1 else "swings"
        candidates.append(
            {
                "detail": f"{swing_count} {swing_label}",
                "recipients": game_players(game),
                "target_game_id": game["game_id"],
                "swing_count": swing_count,
                "swing_total": round(swing_total, 1),
                "sort_key": (swing_count, swing_total, -game_order[game["game_id"]]),
            }
        )
    if not candidates:
        return None
    winner = max(candidates, key=lambda candidate: candidate["sort_key"])
    winner.pop("sort_key", None)
    return winner


def award_recipients(player_or_side: str) -> list[str]:
    recipients = [part.strip() for part in player_or_side.split("/") if part.strip()]
    return recipients or [player_or_side]


def analysis_events_for_game(game: dict[str, Any], analysis_dir: Path) -> list[dict[str, Any]]:
    for analysis_path in analysis_paths_for_game(game, analysis_dir):
        data = load_json(analysis_path, {})
        if isinstance(data, dict) and isinstance(data.get("events"), list):
            return [event for event in data["events"] if isinstance(event, dict)]
    return []


def analysis_paths_for_game(game: dict[str, Any], analysis_dir: Path) -> list[Path]:
    filename_stem = Path(game["filename"]).stem
    return [
        analysis_dir / f"{game['game_id']}.json",
        analysis_dir / f"{filename_stem}.json",
    ]


def winrate_drop_points(event: dict[str, Any]) -> float:
    if "winrate_drop" in event:
        return parse_numeric(event["winrate_drop"])
    before = normalize_winrate_value(event.get("winrate_before", 0))
    after = normalize_winrate_value(event.get("winrate_after", 0))
    return max(0.0, before - after)


def normalize_winrate_value(value: Any) -> float:
    numeric = parse_numeric(value)
    return numeric * 100 if 0 <= numeric <= 1 else numeric


def parse_numeric(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def marathon_award(games: list[dict[str, Any]]) -> dict[str, Any] | None:
    qualifying_games = [game for game in games if game["move_count"] >= MARATHON_MIN_MOVES]
    marathon = max(qualifying_games, key=lambda g: g["move_count"], default=None)
    if not marathon:
        return None
    return {
        "detail": f"{marathon['move_count']} moves",
        "recipients": game_players(marathon),
        "target_game_id": marathon["game_id"],
    }


def build_site(_: argparse.Namespace) -> None:
    games = load_json(GAMES_JSON, [])
    data = build_leaderboard_data(games)
    write_json(LEADERBOARD_JSON, data)
    write_site(data, ANALYSIS_DIR)


def build_sample_site(_: argparse.Namespace) -> None:
    games = load_synthetic_games()
    data = build_leaderboard_data(games, SYNTHETIC_ANALYSIS_DIR)
    write_site(data, SYNTHETIC_ANALYSIS_DIR)


def write_site(data: dict[str, Any], analysis_dir: Path = ANALYSIS_DIR) -> None:
    SITE_DIR.mkdir(parents=True, exist_ok=True)
    copy_site_games(data["games"])
    copy_site_assets()
    write_game_pages(data, analysis_dir)
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


def copy_site_assets() -> None:
    target_dir = SITE_DIR / "assets"
    if target_dir.exists():
        shutil.rmtree(target_dir)
    if not ASSETS_DIR.exists():
        return
    shutil.copytree(ASSETS_DIR, target_dir)


def write_game_pages(data: dict[str, Any], analysis_dir: Path) -> None:
    pages_dir = SITE_DIR / "game"
    if pages_dir.exists():
        shutil.rmtree(pages_dir)
    pages_dir.mkdir(parents=True, exist_ok=True)
    for game in data["games"]:
        page_path = pages_dir / f"{game['game_id']}.html"
        page_path.write_text(render_game_analysis_page(game, data, analysis_dir), encoding="utf-8")


def render_site(data: dict[str, Any]) -> str:
    rows = data["leaderboard"]
    games = data["games"]
    awards = data["edition_awards"]
    awards_title = "Awards"
    award_rules_title = "Final Awards" if data["edition_status"] == "final" else "Provisional Awards"
    award_rules_detail = (
        "These are final for this edition."
        if data["edition_status"] == "final"
        else "These may change until the edition is finalized."
    )
    leader_rows = "\n".join(render_leader_row(i + 1, row, data) for i, row in enumerate(rows))
    game_rows = "\n".join(render_game_row(game, data) for game in games)
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
    .points-badge {{
      display: inline-grid;
      place-items: center;
      width: 24px;
      height: 24px;
      border-radius: 999px;
      background: #e7f2eb;
      border: 1px solid rgba(31, 122, 93, .22);
      font-size: .9rem;
      line-height: 1;
      text-decoration: none;
    }}
    .points-badge:hover {{
      border-color: rgba(31, 122, 93, .42);
      box-shadow: 0 8px 20px rgba(31, 122, 93, .12);
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
    .breakdown-list {{
      display: grid;
      gap: 3px;
      margin: 0;
      padding: 0;
      list-style: none;
    }}
    .breakdown-points {{
      white-space: nowrap;
    }}
    .sub {{
      color: var(--muted);
      font-size: .88rem;
      margin-top: 3px;
    }}
    .result-cell {{
      display: grid;
      gap: 6px;
    }}
    .result-badges {{
      display: flex;
      flex-wrap: wrap;
      gap: 6px;
    }}
    .result-badge {{
      display: inline-grid;
      place-items: center;
      width: 24px;
      height: 24px;
      border-radius: 999px;
      background: #e7f2eb;
      border: 1px solid rgba(31, 122, 93, .22);
      font-size: .9rem;
      line-height: 1;
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
    .award-detail {{
      display: block;
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
          <h3>{award_rules_title}</h3>
          <p>{award_rules_detail}</p>
          <ul class="rules-list">
            {rules_item("🏃 Marathon", "10 pts", f"Longest game of at least {MARATHON_MIN_MOVES} moves.")}
            {rules_item("🧊 Iceberg", "20 pts", "Biggest AI win-rate collapse.")}
            {rules_item("🧘 Zen Master", "20 pts", "Lowest average AI win-rate loss.")}
            {rules_item("🎢 Rollercoaster", "20 pts", f"Most AI win-rate drops of at least {int(ROLLERCOASTER_MIN_SWING)} percentage points.")}
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
    document.querySelectorAll("a[href^='#game-']").forEach((gameLink) => {{
      gameLink.addEventListener("click", () => {{
        window.setTimeout(highlightLinkedGame, 0);
      }});
    }});
    window.addEventListener("hashchange", highlightLinkedGame);
    highlightLinkedGame();
  </script>
</body>
</html>
"""


def render_leader_row(rank: int, row: dict[str, Any], data: dict[str, Any]) -> str:
    class_name = f' class="place-{rank}"' if rank in {1, 2} else ""
    return f"""<tr{class_name}>
  <td class="rank">{rank}</td>
  <td><strong>{escape(row["player"])}</strong></td>
  <td>
    <div class="score-cell">
      {render_score(row, data)}
      {render_breakdown(row, data)}
    </div>
  </td>
</tr>"""


def render_score(row: dict[str, Any], data: dict[str, Any]) -> str:
    provisional = row["award_points"]
    confirmed = row["total_points"] - provisional
    first_penguin_badge = render_first_penguin_badge(row, data)
    if provisional == 0 or data["edition_status"] == "final":
        return f"""<span class="points">{row["total_points"]}</span>{first_penguin_badge}"""
    return f"""<span class="points points-formula">
  <span class="points-confirmed">{confirmed}</span>
  <span class="points-operator">+</span>
  <span class="points-provisional">{provisional}</span>
  <span class="points-operator">=</span>
  <span class="points-total">{row["total_points"]}</span>
</span>{first_penguin_badge}"""


def render_first_penguin_badge(row: dict[str, Any], data: dict[str, Any]) -> str:
    if "🐧 First Penguin" not in row["achievements"]:
        return ""
    first_penguin = data.get("first_penguin")
    game_id = first_penguin.get("game_id") if isinstance(first_penguin, dict) else ""
    if not game_id:
        return """<span class="points-badge" title="First Penguin" aria-label="First Penguin">🐧</span>"""
    return f"""<a class="points-badge" href="#game-{escape(game_id)}" title="First Penguin" aria-label="First Penguin">🐧</a>"""


def rules_item(title: str, points: str, detail: str) -> str:
    return f"""<li>
  <div class="rules-title">
    <span>{escape(title)}</span>
    <strong>{escape(points)}</strong>
  </div>
  <div class="rules-detail">{escape(detail)}</div>
</li>"""


def render_breakdown(row: dict[str, Any], data: dict[str, Any]) -> str:
    edition_status = data["edition_status"]
    awards_title = "Final Awards" if edition_status == "final" else "Provisional Awards"
    awards = render_awards_breakdown(row["awards"], edition_status)
    milestones = render_milestones_breakdown(row, data)
    board_sizes = breakdown_lines([f"{size}x{size}" for size in row["board_sizes"]], "No board sizes yet")
    session_list = breakdown_lines(row["sessions"], "No sessions yet")
    format_list = breakdown_lines(format_labels(row["game_types"]), "No formats yet")
    return f"""<details class="breakdown">
  <summary aria-label="Show points breakdown for {escape(row["player"])}">?</summary>
  <div class="breakdown-panel">
    <div class="breakdown-sections">
      <div class="breakdown-group">
        <div class="breakdown-group-title">Confirmed Points</div>
        {breakdown_section("Sessions", session_list, row["attendance_points"])}
        {breakdown_section("Board sizes", board_sizes, row["board_size_points"])}
        {breakdown_section("Formats", format_list, row["format_points"])}
        {breakdown_section("Milestones", milestones, row["achievement_points"])}
      </div>
      <div class="breakdown-group">
        <div class="breakdown-group-title">{awards_title}</div>
        {breakdown_section("Awards", awards, row["award_points"], provisional=edition_status != "final")}
      </div>
    </div>
  </div>
</details>"""


def render_awards_breakdown(awards: list[str], edition_status: str) -> str:
    if not awards:
        award_label = "final" if edition_status == "final" else "provisional"
        return escape(f"No {award_label} awards")
    return breakdown_lines(awards, "")


def render_milestones_breakdown(row: dict[str, Any], data: dict[str, Any]) -> str:
    if not row["achievements"]:
        return "No milestones yet"
    milestones = []
    first_penguin = data.get("first_penguin")
    for achievement in row["achievements"]:
        if achievement == "🐧 First Penguin" and isinstance(first_penguin, dict):
            drop = first_penguin.get("winrate_drop")
            if drop is not None:
                milestones.append(f"{achievement} ({drop} point drop)")
                continue
        milestones.append(achievement)
    return breakdown_lines(milestones, "")


def breakdown_lines(items: list[Any], empty_label: str) -> str:
    values = [str(item) for item in items if str(item)]
    if not values:
        return escape(empty_label)
    lines = "\n".join(f"<li>{escape(value)}</li>" for value in values)
    return f"""<ul class="breakdown-list">{lines}</ul>"""


def format_labels(game_types: list[str]) -> list[str]:
    labels = {
        "standard": "Standard",
        "pair-go": "Pair Go",
    }
    return [labels.get(game_type, game_type) for game_type in game_types]


def breakdown_section(title: str, detail: str, points: int, provisional: bool = False) -> str:
    points_class = "breakdown-points points-provisional" if provisional else "breakdown-points"
    return f"""<section class="breakdown-section">
  <div class="breakdown-title">
    <span>{escape(title)}</span>
    <strong class="{points_class}">{points} pts</strong>
  </div>
  <div class="breakdown-detail">{detail}</div>
</section>"""


def render_game_analysis_page(game: dict[str, Any], data: dict[str, Any], analysis_dir: Path) -> str:
    title = f"{game['black']} vs {game['white']}"
    metadata = [
        game["session_label"],
        f"{game['board_size']}x{game['board_size']}",
        f"{game['move_count']} moves",
    ]
    if game["game_type"] != "standard":
        metadata.append(str(game["game_type"]))
    metadata_rows = "\n".join(f"<span>{escape(item)}</span>" for item in metadata)
    metadata_rows += f"\n<span>Result: {escape(game['result'] or 'Unknown')}</span>"
    events = sorted(
        analysis_events_for_game(game, analysis_dir),
        key=lambda event: int(event.get("move_number", 0) or 0),
    )
    review_data = game_review_data(game, data, analysis_dir, events)
    award_checks = render_game_award_checks(game, data)
    analysis_rows = "\n".join(render_analysis_event_row(event) for event in events)
    if not analysis_rows:
        analysis_rows = '<tr><td colspan="5">No analysis data available for this game yet.</td></tr>'
    sgf_href = escape(f"../{game['path']}")
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{escape(title)} - Social Go at Kembangan</title>
  <link rel="stylesheet" href="../assets/game-review.css">
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
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      background: radial-gradient(circle at top left, #e7f2eb 0, transparent 30rem), var(--bg);
      color: var(--ink);
    }}
    main {{
      width: min(960px, calc(100% - 32px));
      margin: 0 auto;
      padding: 32px 0 56px;
    }}
    a {{ color: inherit; }}
    .back {{
      color: var(--accent);
      font-weight: 800;
      text-decoration: none;
    }}
    header {{
      display: grid;
      gap: 12px;
      padding: 28px 0 24px;
      border-bottom: 1px solid var(--line);
    }}
    h1 {{
      margin: 0;
      font-size: clamp(2rem, 5vw, 4rem);
      line-height: 1;
      letter-spacing: 0;
    }}
    .meta, .sub {{
      color: var(--muted);
      line-height: 1.45;
    }}
    .meta {{
      display: grid;
      gap: 2px;
    }}
    .actions {{
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      margin-top: 8px;
    }}
    .button {{
      display: inline-flex;
      align-items: center;
      min-height: 38px;
      padding: 0 13px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: var(--panel);
      text-decoration: none;
      font-weight: 800;
    }}
    .button-primary {{
      color: white;
      background: var(--accent);
      border-color: var(--accent);
    }}
    .panel {{
      margin-top: 18px;
      background: color-mix(in srgb, var(--panel) 92%, white);
      border: 1px solid var(--line);
      border-radius: 8px;
      box-shadow: 0 18px 50px rgba(43, 35, 22, .08);
      overflow: hidden;
    }}
    .panel h2 {{
      margin: 0;
      padding: 16px 18px;
      font-size: 1.05rem;
      border-bottom: 1px solid var(--line);
    }}
    .panel-body {{ padding: 16px 18px; }}
    .review-layout {{
      display: grid;
      grid-template-columns: minmax(0, auto) minmax(260px, 1fr);
      gap: 18px;
      align-items: start;
    }}
    .board-panel {{
      display: grid;
      gap: 12px;
    }}
    .board-shell {{
      display: grid;
      justify-content: center;
      overflow-x: auto;
      padding-bottom: 4px;
    }}
    .move-controls {{
      display: flex;
      align-items: center;
      justify-content: center;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .move-controls button {{
      min-height: 34px;
      padding: 0 12px;
      border-radius: 6px;
      border: 1px solid var(--line);
      background: var(--panel);
      color: var(--ink);
      font-weight: 800;
      cursor: pointer;
    }}
    .move-controls button:disabled {{
      color: var(--muted);
      cursor: not-allowed;
    }}
    .selected-analysis {{
      min-height: 42px;
      display: flex;
      justify-content: center;
      gap: 6px;
      flex-wrap: wrap;
      color: var(--muted);
      text-align: center;
      line-height: 1.35;
    }}
    .selected-analysis strong {{
      color: var(--ink);
    }}
    .graph-panel {{
      min-width: 0;
    }}
    .graph-title {{
      margin-bottom: 8px;
      color: var(--muted);
      font-weight: 800;
      font-size: .88rem;
    }}
    .winrate-graph svg {{
      width: 100%;
      height: auto;
      min-height: 170px;
      overflow: visible;
    }}
    .graph-axis {{
      stroke: var(--line);
      stroke-width: 2;
    }}
    .graph-line {{
      fill: none;
      stroke: var(--accent);
      stroke-width: 3;
      stroke-linejoin: round;
      stroke-linecap: round;
    }}
    .graph-hit {{
      fill: transparent;
      cursor: pointer;
    }}
    .graph-hit:hover {{
      fill: rgba(31, 122, 93, .16);
    }}
    .graph-selected {{
      fill: var(--accent-2);
      stroke: white;
      stroke-width: 2;
    }}
    .analysis-row {{
      cursor: pointer;
    }}
    .analysis-row:hover td {{
      background: #e7f2eb;
    }}
    .checks {{
      display: grid;
      gap: 10px;
    }}
    .check {{
      display: grid;
      gap: 3px;
      padding: 12px;
      border-left: 4px solid var(--accent);
      border-radius: 4px;
      background: #e7f2eb;
    }}
    .check-title {{
      display: flex;
      justify-content: space-between;
      gap: 12px;
      font-weight: 800;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 12px 14px;
      text-align: left;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: .78rem;
      text-transform: uppercase;
      letter-spacing: .08em;
    }}
    tr:last-child td {{ border-bottom: 0; }}
    .drop {{
      color: var(--accent-2);
      font-weight: 900;
      white-space: nowrap;
    }}
    @media (max-width: 700px) {{
      main {{ width: min(100% - 20px, 960px); padding-top: 16px; }}
      .review-layout {{ grid-template-columns: 1fr; }}
      th, td {{ padding: 10px 9px; }}
    }}
  </style>
</head>
<body>
  <main>
    <a class="back" href="../index.html#game-{escape(game["game_id"])}">Back to leaderboard</a>
    <header>
      <h1>{escape(title)}</h1>
      <div class="meta">{metadata_rows}</div>
      <div class="actions">
        <a class="button button-primary" href="{sgf_href}" download="{escape(game["filename"])}">Download SGF</a>
      </div>
    </header>

    <section class="panel">
      <h2>Award</h2>
      <div class="panel-body">
        {award_checks}
      </div>
    </section>

    <section class="panel">
      <h2>Board Review</h2>
      <div class="panel-body">
        <div id="game-review-board"><p class="sub">Build the game review assets to show the interactive board.</p></div>
      </div>
    </section>

    <section class="panel">
      <h2>Analysis</h2>
      <div class="panel-body">
        <table>
          <thead><tr><th>Move</th><th>Notation</th><th>Player</th><th>Before</th><th>After</th><th>Drop</th></tr></thead>
          <tbody>{analysis_rows}</tbody>
        </table>
      </div>
    </section>
  </main>
  <script id="game-review-data" type="application/json">{escape_json_script(review_data)}</script>
  <script src="../assets/game-review.js"></script>
</body>
</html>
"""


def render_game_award_checks(game: dict[str, Any], data: dict[str, Any]) -> str:
    checks = []
    first_penguin = data.get("first_penguin")
    if isinstance(first_penguin, dict) and first_penguin.get("game_id") == game["game_id"]:
        checks.append(
            (
                "🐧 First Penguin",
                first_penguin.get("recipient", ""),
                f"Move {first_penguin.get('move_number')} / {first_penguin.get('winrate_drop')} point drop",
            )
        )
    for award in data.get("edition_awards", []):
        if award.get("status") != "current" or award.get("target_game_id") != game["game_id"]:
            continue
        checks.append((award["name"], ", ".join(award["recipients"]), award.get("detail", "")))
    if not checks:
        return '<p class="sub">No awards are currently attached to this game.</p>'
    items = "\n".join(
        f"""<div class="check">
  <div class="check-title"><span>{escape(name)}</span><strong>{escape(holder)}</strong></div>
  <div class="sub">{escape(detail)}</div>
</div>"""
        for name, holder, detail in checks
    )
    return f"""<div class="checks">{items}</div>"""


def game_review_data(
    game: dict[str, Any],
    data: dict[str, Any],
    analysis_dir: Path,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "boardSize": game["board_size"],
        "initialMove": initial_review_move(game, data, events),
        "moves": parse_sgf_moves(ROOT / game["path"], game["board_size"]),
        "events": events,
    }


def initial_review_move(game: dict[str, Any], data: dict[str, Any], events: list[dict[str, Any]]) -> int:
    first_penguin = data.get("first_penguin")
    if isinstance(first_penguin, dict) and first_penguin.get("game_id") == game["game_id"]:
        return int(first_penguin.get("move_number", 0) or 0)
    award_moves = [
        int(event.get("move_number", 0) or 0)
        for event in events
        if winrate_drop_points(event) == max((winrate_drop_points(candidate) for candidate in events), default=0)
    ]
    return award_moves[0] if award_moves else min(game["move_count"], 1)


def render_analysis_event_row(event: dict[str, Any]) -> str:
    move_number = escape(event.get("move_number", ""))
    return f"""<tr class="analysis-row" data-move-number="{move_number}">
  <td>{move_number}</td>
  <td>{escape(event.get("move", ""))}</td>
  <td>{escape(event.get("player", ""))}</td>
  <td>{format_analysis_number(event.get("winrate_before"))}</td>
  <td>{format_analysis_number(event.get("winrate_after"))}</td>
  <td class="drop">{format_analysis_number(event.get("winrate_drop"))}</td>
</tr>"""


def format_analysis_number(value: Any) -> str:
    try:
        return escape(f"{float(value):.1f}")
    except (TypeError, ValueError):
        return ""


def escape_json_script(data: Any) -> str:
    return (
        json.dumps(data, ensure_ascii=False)
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
        .replace("&", "\\u0026")
    )


def render_game_row(game: dict[str, Any], data: dict[str, Any]) -> str:
    metadata = f"{game['board_size']}x{game['board_size']} / {game['move_count']} moves"
    if game["game_type"] != "standard":
        metadata = f"{metadata} / {game['game_type']}"
    game_href = escape(f"game/{game['game_id']}.html")
    game_title = f"{escape(game['black'])} vs {escape(game['white'])}"
    result_badges = render_game_result_badges(game, data)
    return f"""<tr class="game-row" id="game-{escape(game["game_id"])}">
  <td>{escape(game["session_label"])}</td>
  <td><a class="game-link" href="{game_href}"><strong>{game_title}</strong></a><div class="sub">{escape(metadata)}</div></td>
  <td><div class="result-cell"><span>{escape(game["result"])}</span>{result_badges}</div></td>
</tr>"""


def render_game_result_badges(game: dict[str, Any], data: dict[str, Any]) -> str:
    badges = []
    first_penguin = data.get("first_penguin")
    if isinstance(first_penguin, dict) and first_penguin.get("game_id") == game["game_id"]:
        badges.append(("🐧", "First Penguin"))
    for award in data.get("edition_awards", []):
        if award.get("status") != "current" or award.get("target_game_id") != game["game_id"]:
            continue
        badges.append((award_icon(award["name"]), award["name"]))
    if not badges:
        return ""
    badge_html = "".join(
        f"""<span class="result-badge" title="{escape(title)}" aria-label="{escape(title)}">{escape(icon)}</span>"""
        for icon, title in badges
    )
    return f"""<div class="result-badges">{badge_html}</div>"""


def award_icon(name: str) -> str:
    return str(name).split(" ", 1)[0]


def render_award_card(award: dict[str, Any], edition_status: str) -> str:
    class_name = "award award-pending" if award["status"] == "pending" else "award"
    status_label = "" if edition_status == "final" else "Currently"
    if award["status"] == "pending":
        status_label = "Not awarded" if edition_status == "final" else "Pending"
    points_class = "award-points"
    if award["status"] != "pending" and edition_status != "final":
        points_class = "award-points points-provisional"
    recipients = ", ".join(award["recipients"])
    if recipients and award.get("detail"):
        winner_text = f"{status_label}: {recipients}" if status_label else recipients
        detail = f"""{escape(winner_text)}
    <span class="award-detail">{escape(award["detail"])}</span>"""
    elif recipients:
        winner_text = f"{status_label}: {recipients}" if status_label else recipients
        detail = escape(winner_text)
    else:
        detail_text = str(award["detail"])
        if award["status"] == "pending" and edition_status == "final":
            detail_text = detail_text.removeprefix("Pending: ").removeprefix("Pending ")
            detail = escape(f"{status_label}: {detail_text}")
        else:
            detail = escape(detail_text)
    tag = "a" if award.get("target_game_id") else "div"
    href = f' href="#game-{escape(award["target_game_id"])}"' if award.get("target_game_id") else ""
    return f"""<{tag} class="{class_name}"{href}>
  <div class="award-title">
    <span>{escape(award["name"])}</span>
    <strong class="{points_class}">+{award["points"]} pts</strong>
  </div>
  <span class="award-meta">{detail}</span>
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

    analyze = subparsers.add_parser("analyze-katago", help="Analyze a folder of SGF files with KataGo")
    analyze.add_argument("session_dir", help="Folder containing SGF files")
    analyze.add_argument("--date", help="Session date, for example 2026-08-27")
    analyze.add_argument("--label", help="Display label, for example '27 Aug'")
    analyze.add_argument("--analysis-dir", default=str(ANALYSIS_DIR.relative_to(ROOT)), help="Folder for analysis JSON files")
    analyze.add_argument("--metadata", default=str(METADATA_CSV.relative_to(ROOT)), help="CSV file with game metadata")
    analyze.add_argument("--katago", default="katago", help="KataGo executable")
    analyze.add_argument("--config", default=str(DEFAULT_KATAGO_CONFIG), help="KataGo analysis config")
    analyze.add_argument("--model", default=str(DEFAULT_KATAGO_MODEL), help="KataGo model file")
    analyze.add_argument("--visits", type=int, default=16, help="KataGo visits per analyzed position")
    analyze.add_argument("--rules", default="Chinese", help="Rules to use for analysis")
    analyze.set_defaults(func=analyze_session_with_katago)

    sample_analysis = subparsers.add_parser("analyze-sample-katago", help="Analyze synthetic fixture games with KataGo")
    sample_analysis.add_argument("--katago", default="katago", help="KataGo executable")
    sample_analysis.add_argument("--config", default=str(DEFAULT_KATAGO_CONFIG), help="KataGo analysis config")
    sample_analysis.add_argument("--model", default=str(DEFAULT_KATAGO_MODEL), help="KataGo model file")
    sample_analysis.add_argument("--visits", type=int, default=16, help="KataGo visits per analyzed position")
    sample_analysis.add_argument("--rules", default="Chinese", help="Rules to use for analysis")
    sample_analysis.set_defaults(func=analyze_sample_with_katago)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
