# Session: Project Setup
Date: 2026-08-14
Project: NFL Betting Screener
Goal: Set up the NFL betting screener project, modeled on the existing stock-screener project's structure, with two distinct strategies (player prop trends and a power-rating model for sides/totals/moneylines).
Continuing from: first session

## Status at Start
- Last completed: N/A — first session
- Pending: N/A
- Blockers: None

## Work Log
- Clarified scope: project folder was empty; user confirmed they wanted stock-screener used as a structural model, not literally continued
- Gathered requirements via Q&A: screen all bet types (spreads, totals, moneyline, player props); use a real predictive power-rating model for sides/totals/moneylines; use a simpler trend-based model (player average vs. opponent defense allowed) for player props; free-tier data sources (nfl_data_py for stats, The Odds API for odds); email to bradleyford5@hotmail.com; run Monday/Wednesday/Friday/Saturday
- Built full project scaffold mirroring stock-screener's layout: CLAUDE.md, requirements.txt, .env.example, .gitignore, main.py, model/ (player_trends.py, power_ratings.py), screener/ (cache.py, fetch_stats.py, fetch_odds.py, team_map.py, pipeline.py, scoring.py), email_report/ (formatter.py, send.py, error_alert.py), .github/workflows/screener.yml (cron: Mon/Wed/Fri/Sat, currently inert — no GitHub remote or secrets yet)
- Created a dedicated conda environment `nflbetting` (Python 3.11, matching stock-screener's env pattern) and installed dependencies
- Verified real data end-to-end: weekly player stats, schedules, team power ratings, matchup predictions, defense-allowed calculations, and the player trend screener all produce correct, sane output against live nfl_data_py data
- Discovered and handled a data-availability quirk: nfl_data_py's player-level weekly stats currently only go through the 2024 season (2025 hasn't been published to the underlying nflverse data source yet), even though schedules already show 2025 as complete. Made `get_weekly_player_stats` / `get_schedules` fetch year-by-year and skip any year with no data yet, so the screener always falls back gracefully and will pick up newer seasons automatically once published.
- The Odds API integration (screener/fetch_odds.py) is built against documented request/response formats but has NOT been tested live — no API key configured yet
- git init + first commit made (branch: master, matching stock-screener's convention)
- Bradley provided The Odds API key and Gmail credentials (address + app password); added to local `.env`
- Verified odds API live: `get_events`/`get_game_odds` work, response format matches the parsing code exactly (h2h/spreads/totals)
- Verified player props endpoint is reachable but returns zero bookmakers for all near-term games — expected, since sportsbooks don't post NFL prop lines this far before the season (matches Bradley's own stated timeline of props appearing closer to game week)
- Ran the full game screener against live odds — first result was a red flag: 375 of ~816 possible bets flagged (46%), way too many to be real signal. Measured the model's natural noise vs. the market (~3.0 pt stdev on spreads, ~2.3 pt on totals, ~0.09 win-prob on moneylines) and found the original thresholds were tighter than that noise band, so it was flagging noise, not edges. Recalibrated thresholds to ~1.5-2x the measured noise, cutting flags to a more plausible ~8% (66 games)
- Also flagged an unresolved model limitation: several teams (MIA, CIN, JAX, ARI) showed up as "value" against many different opponents, a sign the model isn't strength-of-schedule adjusted yet — documented in CLAUDE.md as a known limitation, not fixed this session
- Fixed a docs bug: the documented `python email_report/send.py --test` command doesn't work (import error) — `python -m email_report.send --test` does; updated CLAUDE.md and main.py's docstring
- Sent a live test email (with Bradley's explicit go-ahead) — delivered successfully to bradleyford5@hotmail.com

## Pending
- Confirm with Bradley that the test email actually arrived and reads well
- Once player props start appearing (closer to the season / Wednesday runs), verify `run_props_screener` end-to-end against real prop lines — untested so far since no props exist yet this early
- Power-rating model needs strength-of-schedule adjustment (e.g. a proper Massey/Elo-style iterative rating) and a real backtest against past season outcomes before picks should be trusted with money — current thresholds only reduce false positives, they don't prove the model has skill
- GitHub Actions scheduling (.github/workflows/screener.yml) is scaffolded but inert — needs a GitHub remote + repo secrets (ODDS_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD) before it can run automatically; do not push/enable without confirming with Bradley first
- Known data lag: player prop trend model is currently limited to 2024 season stats until nflverse publishes 2025 weekly data — worth rechecking as the season approaches

## Blockers
None — core pipeline is verified working end-to-end (except props, which just has no live data to test against yet). Remaining work is model quality (schedule adjustment, backtesting), not plumbing.

## End of Session
[/session-end will fill this in]
