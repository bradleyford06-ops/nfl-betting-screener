# NFL Betting Screener

## What This Project Does
Screens NFL betting markets for value opportunities using two separate strategies, and delivers a ranked pick list by email on Monday, Wednesday, Friday, and Saturday.

## Vision & Product Lead
Bradley Ford — makes all betting-logic and product decisions. Not a developer.

## Technical Lead
Claude — responsible for all code, architecture, and technical decisions.

## Two Strategies This Screener Identifies

### 1. Player Props — Trend-Based
Simple, explainable comparison: a player's rolling/season average in a stat category vs. what the opposing defense allows in that same category. If both point the same direction and the sportsbook's line sits on the wrong side of that gap, it's flagged.

Example: a running back averages 60 rushing yards/game, the opposing defense allows well above average rushing yards, and the book's line is 55.5 — that's a flagged value bet.

No custom point-prediction model here — just averages vs. line, kept easy to sanity-check by eye.

### 2. Sides, Totals & Moneylines — Predictive Model
A power-rating model built from team stats (offensive/defensive efficiency, scoring, etc.) generates our own predicted spread and total for each game. Flagged when our number disagrees meaningfully with the market's line — the bigger the gap, the higher it ranks.

This is the harder build and will take real iteration before it's trustworthy with money. Starts simple (basic power ratings) and gets refined over time.

## Tech Stack
- Language: Python 3 (via Anaconda)
- Team/Player Stats: `nfl_data_py` (free, pulls from the nflverse/nflfastR data project)
- Betting Odds: The Odds API (free tier to start — 500 requests/month; may need to revisit if player-prop coverage burns through the quota)
- Storage: SQLite (local cache, prevents redundant API calls and preserves history for trend calculations)
- Email: SMTP via Gmail
- Scheduling: Cron job or Claude scheduled task

## Screening Criteria

**Player Props (trend model):**
- Player must have enough recent games to establish a reliable average (season-to-date, weighted toward recent games)
- Opposing defense's allowed average in that stat category, same window
- Flag when the sportsbook line sits meaningfully below (or above, for unders) what both signals support

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
- `email_report/` — email formatting and delivery
- `scheduler/` — cron/scheduling setup
- `docs/` — decisions log and project notes
- `.claude/memory/sessions/` — session history

## Commands
- Run screener manually: `python main.py`
- Run screener and send email: `python main.py --send`
- Run props only: `python main.py --props-only`
- Run sides/totals only: `python main.py --games-only`

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
**Status:** Project setup phase — folder structure and vision doc created, screener not yet built.
**Next step:** Build the data-fetching layer (stats + odds), then get the two models producing output printed to screen, before wiring up email/scheduling.
**Phase:** 1 of 2 (Phase 1 = working screener with manual runs, Phase 2 = automated email delivery on the Mon/Wed/Fri/Sat schedule)
