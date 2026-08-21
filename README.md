# Social Go Leaderboards

Local scripts for processing Social Go SGF submissions and generating a static leaderboard website.

## Workflow

1. Put session SGFs into a dated folder:

```text
games/
  real/
    2026-08-27/
    alice-vs-bob.sgf
    chen-vs-dee.sgf
```

2. Process that session:

```bash
python3 scripts/social_go.py process games/real/2026-08-27 --date 2026-08-27 --label "27 Aug"
```

3. Rebuild the website:

```bash
python3 scripts/social_go.py build-site
```

4. Open:

```text
output/site/index.html
```

The scripts are incremental. If an SGF file has already been processed and its contents have not changed, it will not be duplicated.

To calculate AI win-rate drops for that session, run:

```bash
python3 scripts/social_go.py analyze-katago games/real/2026-08-27 --date 2026-08-27 --label "27 Aug"
```

This writes one analysis JSON file per SGF under `analysis/`. The website uses those files for First Penguin.

Synthetic SGFs live under `fixtures/synthetic-games/` and are not part of the real leaderboard workflow.

To preview the site with synthetic data:

```bash
python3 scripts/social_go.py analyze-sample-katago
python3 scripts/social_go.py build-sample-site
```

This builds `output/site` using fixture games without changing the real processed data files.

## GitHub Pages

The repository includes a GitHub Actions workflow at `.github/workflows/deploy-pages.yml`.

When changes are pushed to `main`, GitHub Actions will:

1. Run `python3 scripts/social_go.py build-site`
2. Upload `output/site`
3. Deploy it to GitHub Pages

The build copies downloadable SGF files into:

```text
output/site/games/
```

In GitHub, enable Pages using **GitHub Actions** as the source.

## Edition Status

The awards section uses `data/settings.json`.

```json
{
  "edition_status": "in_progress"
}
```

Use `in_progress` while sessions are still ongoing. Awards that can still change are shown as provisional.

After the final session has been processed, change it to:

```json
{
  "edition_status": "final"
}
```

The website will then treat award points as final.

## Current Scoring

Confirmed points are locked once earned.

- Attend a session: 10 points
- There is no attend-every-session bonus
- Play 9x9: 10 points
- Play 13x13: 10 points
- Play 19x19: 10 points
- Play Pair Go: 20 points
- 🧭 Board Nomad: +30 points for playing all three board sizes
- 🗺️ Go Explorer: +30 points for playing Standard and Pair Go
- 🐧 First Penguin: 20 points for the first dramatic AI win-rate collapse

Provisional awards may change until the edition is finalized.

- 🧊 Iceberg Award: 20 points for the biggest AI win-rate collapse
- 🧘 Zen Master Award: 20 points for the most accurate game
- 🎢 Rollercoaster Award: 20 points for the most AI win-rate swings
- 📸 Photo Finish Award: 20 points for the closest game
- 🏃 Marathon Award: 10 points for the longest game processed so far, minimum 100 moves

Awards that need analysis currently appear as pending until their metrics are supported. First Penguin can be calculated from saved analysis events.

## Analysis Data

First Penguin is calculated from saved analysis files. The `analyze-katago` command starts KataGo once, asks it to analyze every turn in each SGF, then records each player's win-rate loss after their own moves.

```text
analysis/
  alice-vs-bob.json
```

The generated shape is:

```json
{
  "generated_by": "katago",
  "katago": {
    "visits": 16,
    "winrate_perspective": "black"
  },
  "summary": {
    "biggest_winrate_loss": {
      "move_number": 24,
      "player": "Alice",
      "winrate_drop": 47.0
    }
  },
  "events": [
    {
      "move_number": 24,
      "player": "Alice",
      "winrate_before": 92.0,
      "winrate_after": 45.0,
      "winrate_drop": 47.0
    }
  ]
}
```

First Penguin is awarded to the earliest player whose win rate drops by at least 40 percentage points in one move.

## Optional Game Metadata

Some leaderboard facts are not always stored in SGF files. You can add them in `data/game_metadata.csv`.

```csv
filename,game_type
alice-vs-bob.sgf,pair-go
```

If metadata is missing, games default to `standard`.
