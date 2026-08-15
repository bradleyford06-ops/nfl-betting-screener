# NFL Betting Screener

## What This Project Does
Screens NFL betting markets for value opportunities using two separate strategies, and delivers a ranked pick list by email on Monday, Wednesday, Friday, and Saturday.

## Vision & Product Lead
Bradley Ford — makes all betting-logic and product decisions. Not a developer.

## Technical Lead
Claude — responsible for all code, architecture, and technical decisions.

## Two Strategies This Screener Identifies

### 1. Player Props — Trend-Based
A player's recent average in a stat category vs. what the opposing defense allows in that same category. If both point the same direction and the sportsbook's line sits on the wrong side of that gap, it's flagged.

Example: a running back averages 60 rushing yards/game, the opposing defense allows well above average rushing yards, and the book's line is 55.5 — that's a flagged value bet.

**Opponent-adjusted (2026-08-15):** both sides of the comparison correct for strength of schedule, the same fix applied to the game model — a player's average is corrected using how tough each of their own past opponents was, and a defense's "allowed" number is corrected for the strength of the offenses it actually faced (not just a raw average). Reuses the same iterative rating math as the game model, run separately per stat/position.

**Small samples & rookies:** a player needs at least 1 game of NFL history to get a trend at all, but the required edge scales up sharply for thin samples (a 1-game sample needs ~2.8x the usual gap before it's trusted). True rookie debuts (zero NFL games) can't be trended at all — rather than vanishing silently, they're listed in a separate "no data yet" section of the report so it's clear the model has no opinion on them, not that it missed them.

This has moved a step past "just averages vs. line, easy to eyeball" to fix a real bias, but stays simpler than the game model — still no historical backtest exists for props (no free source of historical player-prop lines), so treat picks as reasoned, not proven.

### 2. Sides, Totals & Moneylines — Predictive Model
An opponent-adjusted power-rating model (each team's offense/defense rating accounts for who they actually played, not just raw scoring averages) generates our own predicted spread and total for each game. Flagged when our number disagrees meaningfully with the market's line — the bigger the gap, the higher it ranks.

**Backtested against 2019-2024 (see `backtest/`):**
- **Spreads:** real, monotonic edge — bigger disagreements win more often. Live threshold flags disagreements of 8+ points (156 bets in the backtest, 56.4% win rate, +9.6% ROI).
- **Moneylines:** disabled. Even after fixing the schedule-adjustment gap, no threshold showed a clean, well-sampled edge — flagging it lost money.
- **Totals:** kept enabled per Bradley's explicit call, but the backtest found no edge at any threshold tested (flat ~49-50% win rate, negative ROI throughout). Treat its picks as unproven/exploratory, not a validated signal.

This is the harder build and took real iteration before the spread side became trustworthy enough to flag with any confidence — see `backtest/run_backtest.py` to re-validate after any model change.

## Tech Stack
- Language: Python 3 (via Anaconda)
- Team/Player Stats: `nfl_data_py` (free, pulls from the nflverse/nflfastR data project)
- Betting Odds: The Odds API (free tier to start — 500 requests/month; may need to revisit if player-prop coverage burns through the quota)
- Storage: SQLite (local cache, prevents redundant API calls and preserves history for trend calculations)
- Email: SMTP via Gmail
- Scheduling: Cron job or Claude scheduled task

## Screening Criteria

**Player Props (trend model):**
- Player's opponent-adjusted average over their last 8 games (or fewer, down to 1, with a proportionally bigger required edge)
- Opposing defense's opponent-adjusted allowed average in that stat category
- Flag when the sportsbook line sits meaningfully below (or above, for unders) what both signals support
- True rookie debuts (zero NFL games) are listed separately as "no data yet," not flagged

**Sides / Totals / Moneylines (power-rating model):**
- Team power ratings from scoring efficiency, yardage efficiency, turnover margin, and recent form
- Predicted spread/total generated per matchup
- Flag when market line disagrees meaningfully with our predicted number

## Email Delivery
- **To:** bradleyford5@hotmail.com
- **Schedule:**
  - **Monday** — odds open for the upcoming week (sides, totals, moneylines)
  - **Wednesday** — player prop lines typically appear; run again
  - **Friday** — re-run as lines move
  - **Saturday** — re-run closer to Sunday's slate
- **Format:** Ranked pick list, split by strategy (Player Props / Sides & Totals)
  - Each pick: plain-English explanation of why it was flagged
  - Delta section: new picks since last report, picks that dropped off or lines that moved against us

## Key Directories
- `data/` — SQLite cache, raw data files
- `model/` — the two prediction strategies
  - `model/player_trends.py` — player-vs-defense trend comparison
  - `model/power_ratings.py` — team power-rating predictive model
- `screener/` — data fetching, orchestration, scoring
  - `screener/fetch_stats.py` — pulls team/player stats via `nfl_data_py`
  - `screener/fetch_odds.py` — pulls odds via The Odds API
  - `screener/pipeline.py` — runs both models, combines results
  - `screener/scoring.py` — ranks flagged bets
- `backtest/` — walk-forward backtest of the game model against past seasons (`run_backtest.py` to run it, `simulate.py` for the simulation logic)
- `email_report/` — email formatting and delivery
- `scheduler/` — cron/scheduling setup
- `docs/` — decisions log and project notes
- `.claude/memory/sessions/` — session history

## Commands
- Run screener manually: `python main.py`
- Run screener and send email: `python main.py --send`
- Run props only: `python main.py --props-only`
- Run sides/totals only: `python main.py --games-only`
- Test email without running screener: `python -m email_report.send --test`
- Backtest the game model against past seasons: `python -m backtest.run_backtest`

## Conventions
- Use descriptive variable names — no single-letter variables
- Every function needs a one-line plain-English comment explaining what it does
- Log errors clearly so a non-technical user can understand what went wrong
- Never hardcode API keys — always use environment variables or a `.env` file

## Hard Rules
- NEVER commit `.env` files or API keys to git
- NEVER send email to any address other than bradleyford5@hotmail.com without explicit approval
- NEVER delete cached data without confirming with Bradley first
- ALWAYS test email formatting before enabling scheduled sends
- This screener produces informational picks only — it never places bets or touches any sportsbook account

## Current Work Context
**Status:** Core pipeline built, verified against live data, and the game model has been backtested against 6 real seasons. API keys and Gmail app password are configured in `.env`. Test email delivery confirmed working. Spread screening now has real backtested evidence of an edge (see above); moneyline is disabled; totals stay on by product decision despite no proven edge. Player props screener is built but untested against real props data — sportsbooks don't post NFL prop lines this far before the season (props start appearing closer to game week, matching the Wednesday run).
**Next step:** Once the season is closer and player props start appearing, verify the props screener end-to-end. Consider re-backtesting periodically as more of the 2026 season accumulates, since ratings will shift from prior-season data onto current-season data.
**Phase:** 1 of 2 (Phase 1 = working, backtested screener with manual runs — done for the spread strategy; Phase 2 = automated email delivery on the Mon/Wed/Fri/Sat schedule — built but not activated, needs a GitHub remote + secrets)
