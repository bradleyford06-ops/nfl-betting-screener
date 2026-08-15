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

## Pending
- Bradley needs to sign up for a free The Odds API key (the-odds-api.com, 500 requests/month free tier) and add it to a local `.env` file as `ODDS_API_KEY`
- Gmail app password needs to be set up and added to `.env` (GMAIL_ADDRESS, GMAIL_APP_PASSWORD) before email sending can be tested
- Once an odds API key exists: test `run_props_screener` and `run_game_screener` live, confirm the team-name mapping (screener/team_map.py) and player-prop JSON parsing match real API responses exactly
- Power-rating model is a first-pass/simple version (average points scored/allowed blended with opponent's allowed average) — flagged in CLAUDE.md as needing real iteration before it's trustworthy with money
- GitHub Actions scheduling (.github/workflows/screener.yml) is scaffolded but inert — needs a GitHub remote + repo secrets (ODDS_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD) before it can run automatically; do not push/enable without confirming with Bradley first
- Known data lag: player prop trend model is currently limited to 2024 season stats until nflverse publishes 2025 weekly data — worth rechecking as the season approaches

## Blockers
None currently — next steps just need API keys from Bradley.

## End of Session
[/session-end will fill this in]
