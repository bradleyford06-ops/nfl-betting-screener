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
- Bradley asked "what's next" — presented the choice between backtesting first vs. building schedule-adjustment first vs. leaving the model alone; he chose backtesting first
- Built `backtest/` (simulate.py + run_backtest.py): a walk-forward simulation using nfl_data_py's historical schedules, which turn out to include closing spread/total/moneyline lines AND the actual historical odds paid on each side — so the backtest grades real ROI, not just prediction accuracy. Ratings for each test game are built only from games before it (no lookahead). Refactored power_ratings.py to split `build_team_games`/`ratings_from_team_games` out of `compute_team_ratings` so the backtest could reuse the rating logic with a per-game cutoff.
- First backtest (2019-2024, 882 bets) was damning for moneyline: 23.8% win rate, -20.8% ROI. Diagnosed why: 314 of 319 flagged ML bets were underdog picks — the model's win probabilities never got as extreme as the market's on lopsided games because it isn't opponent-adjusted, so it read every heavy favorite as "overpriced." Spread showed a real edge (55.1% win rate, +5.8% ROI, 314 bets); totals were underwater (-11% ROI).
- Presented these findings to Bradley; he chose to turn off moneyline and build the schedule-adjustment fix (rather than just widening thresholds or shipping spreads-only)
- Turned off moneyline screening in `screener/pipeline.py` (removed the h2h market fetch and screen_moneyline call), with a comment citing the backtest numbers so a future session understands why
- Rebuilt the rating system in `model/power_ratings.py`: replaced the naive scoring average with an iterative, opponent-adjusted offense/defense rating (each team's rating is corrected for how tough their actual opponents were, not just raw points scored/allowed) — a simplified Massey-style power rating, ~15 iterations to converge, vectorized with pandas for backtest performance (full 6-season backtest runs in ~35 seconds)
- Re-ran the backtest with the new model: moneyline win rate roughly doubled (23.8% → 42.1%) confirming the diagnosis was right, but still no clean edge at any threshold. Spread's previous edge disappeared at the *old* threshold (5.0), so instead of trusting that at face value, ran a full threshold sweep (0 to 12+ points) — found a genuine monotonic relationship: win rate climbs from ~50% at low thresholds to 56-60% at edge>=8-10. This is a much stronger, harder-to-fake signal than a single lucky threshold. Raised SPREAD_EDGE_THRESHOLD to 8.0 (156 bets, 56.4% win rate, +9.6% ROI in-sample). Total showed NO edge at any threshold from 0-8 — flat ~49-50% win rate, negative ROI throughout.
- Asked Bradley what to do about totals given the flat/negative sweep results; he chose to leave totals enabled anyway despite the lack of backtest support — documented clearly in power_ratings.py and CLAUDE.md that this was his explicit call against the data, so totals picks should be treated as unproven/exploratory, not validated
- Verified the live pipeline still runs correctly end-to-end with the new model (156 spread/total flags on the current preseason slate)
- Updated CLAUDE.md's "Two Strategies" and "Current Work Context" sections with the backtest results and current model status

## Pending
- Confirm with Bradley that the test email actually arrived and reads well
- Once player props start appearing (closer to the season / Wednesday runs), verify `run_props_screener` end-to-end against real prop lines — untested so far since no props exist yet this early
- Consider re-backtesting periodically as the 2026 season accumulates real games, since ratings will shift from prior-season data onto current-season data
- GitHub Actions scheduling (.github/workflows/screener.yml) is scaffolded but inert — needs a GitHub remote + repo secrets (ODDS_API_KEY, GMAIL_ADDRESS, GMAIL_APP_PASSWORD) before it can run automatically; do not push/enable without confirming with Bradley first
- Known data lag: player prop trend model is currently limited to 2024 season stats until nflverse publishes 2025 weekly data — worth rechecking as the season approaches
- Totals screener remains enabled despite backtest showing no edge (Bradley's explicit decision) — worth revisiting if he wants to eventually improve or retire it

## Blockers
None. Core pipeline verified end-to-end and the game model's spread strategy now has real backtested evidence behind it. Remaining work is props verification (blocked on real prop-line data existing) and Phase 2 automation (blocked on Bradley wanting to activate it).

## End of Session
[/session-end will fill this in]
