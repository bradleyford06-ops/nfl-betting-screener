# NFL Betting Screener

## What This Project Does
Screens NFL betting markets for value opportunities using two separate strategies. Runs automatically once a day via GitHub Actions: emails standout picks and publishes a full interactive dashboard.

- **Live dashboard:** https://bradleyford06-ops.github.io/nfl-betting-screener/ — browse every upcoming week, drill into any game to see its props, filter by stat/source, sorted by edge; plus a season performance tab (win rate/ROI per strategy, reconciled automatically as games complete).
- **GitHub repo:** https://github.com/bradleyford06-ops/nfl-betting-screener (public — the repo needs to be public for free GitHub Pages hosting; Bradley made this call explicitly knowing the tradeoff against a paid private-Pages plan)
- **Email:** still goes out on every run too, for standouts — see Email Delivery below.

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

**Per-position/stat thresholds (2026-08-15):** a flat 8%/5% edge threshold applied to every stat category ignored that natural volatility varies a lot by position — a synthetic-line backtest (`backtest/run_props_backtest.py`, using each player's own trailing average as a stand-in for a market line, since no free historical prop-line source exists) found RB receiving yards is ~2.6x noisier than WR, and QB rushing yards is ~3.7x noisier than RB rushing. Thresholds are now calibrated per (position, stat) combo — see `POSITION_STAT_THRESHOLDS` in `model/player_trends.py`:
- WR/RB/TE receiving yards, RB rushing yards, QB passing yards: raised well above the old flat threshold, backed by a 58-62% (71% for QB passing, smaller sample) hit rate against the synthetic backtest
- **QB rushing yards:** the weakest, noisiest signal tested (54.5% best case) — kept at the original loose threshold so enough picks flow through to watch for a real trend as the season provides live data, but routed to its own "speculative" report section rather than the main list, since Bradley wants to make his own call on these rather than have the model treat them as equally trustworthy
- Any combo not yet backtested (TDs, completions, attempts, receptions) stays on the original flat 8%/5% guess

This has moved a step past "just averages vs. line, easy to eyeball" to fix real biases, but stays simpler than the game model — the synthetic-line backtest shows the signal has *some* real predictive content, but beating a player's own long-run average is a much lower bar than beating an actual sportsbook line (books already price in most of what a trend captures), so treat picks as reasoned and backtest-informed, not proven the way spreads are.

**Coverage model — a second, independent signal for receptions/receiving yards (2026-08-16):** Bradley's own idea, refined through two build attempts. The core insight: how a player performs against zone vs. man coverage is a real, meaningfully different signal (e.g. one real player in testing caught 88% of targets vs. zone but only 65% vs. man) — and it's freely available in nflverse's play-by-play data (`defense_man_zone_type`/`defense_coverage_type`, joinable to plays via `receiver_player_id`), which was a genuine surprise since that level of charting is usually paywalled.

- **First attempt (rejected):** a full bottom-up simulation — estimate team pace, then pass/run split, then defensive coverage mix, then player target share, multiplying all four together before applying the player's coverage-specific efficiency. Backtested at ~51-53% hit rate that got *worse* with bigger edges (46-48%) — the opposite of real signal. Diagnosis: multiplying together several independently-estimated numbers compounds their noise instead of canceling it out. Kept in `model/coverage_sim.py` for reference (`screen_coverage_prop`), not used live.
- **Simplified version (live):** instead of rebuilding player volume from scratch, use the player's own already-proven opponent-adjusted target trend (same machinery as the main trend model, just applied to the `targets` stat), and apply coverage as a single efficiency modifier on top: `predicted value = player's recent avg targets × [defense zone-rate × player's zone efficiency + (1 - zone-rate) × player's man efficiency]`. Backtested cleanly across all 6 receiving combos (WR/RB/TE × receptions/receiving yards, 2022-2024): hit rate climbs monotonically from ~53-55% up to 57-64% as the edge threshold rises — real signal, on par with or better than the trend model for receptions specifically. `COVERAGE_EDGE_THRESHOLD = 0.20` in `model/coverage_sim.py` balances hit rate against sample size across all six.
- **Rollout:** runs as a fully separate signal alongside the trend model (Bradley's explicit choice over requiring agreement or replacing the trend model) — own report section, own ranking, not blended. Only covers receptions/receiving yards; rushing and passing props aren't touched by coverage schemes the same way, so the trend model remains the only signal there.
- **Caveat:** same as the trend model — validated against a synthetic line (player's own trailing average), not a real sportsbook line, since no free historical prop-line source exists. Real market validation has to wait for the season.

### 2. Sides, Totals & Moneylines — Predictive Model
An opponent-adjusted power-rating model (each team's offense/defense rating accounts for who they actually played, not just raw scoring averages) generates our own predicted spread and total for each game. Flagged when our number disagrees meaningfully with the market's line — the bigger the gap, the higher it ranks.

**Backtested against 2019-2024 (see `backtest/`):**
- **Spreads:** real, monotonic edge — bigger disagreements win more often. Live threshold flags disagreements of 8+ points (156 bets in the backtest, 56.4% win rate, +9.6% ROI).
- **Moneylines:** disabled. Even after fixing the schedule-adjustment gap, no threshold showed a clean, well-sampled edge — flagging it lost money.
- **Totals:** kept enabled per Bradley's explicit call, but the backtest found no edge at any threshold tested (flat ~49-50% win rate, negative ROI throughout). Treat its picks as unproven/exploratory, not a validated signal.

This is the harder build and took real iteration before the spread side became trustworthy enough to flag with any confidence — see `backtest/run_backtest.py` to re-validate after any model change.

**Moneyline root-cause investigation (2026-08-30) — do not re-open this without new evidence.** Dug into *why* moneyline shows no edge, not just confirming it doesn't: (1) rebuilt the spread→win-probability conversion using a proper logistic fit on real outcomes instead of the current fixed-std-dev normal curve — barely moved the accuracy score (Brier 0.228 → 0.226), so it's not a math/calibration bug; (2) the market's own win-probability accuracy is clearly better than ours either way (Brier 0.210 vs. 0.226-0.228); (3) the damning pattern: when our model disagrees with the market *more* strongly, it gets *more* wrong, not less (44% win rate on any disagreement, dropping to 33% then 27% at the largest disagreements) — real noise would hover near 50%, this is a systematic bias; (4) traced the bias to time-in-season — our picks vs. the market's picks perform similarly in Week 1, but the gap widens every week after (by 9+ games into the season, market picks win 70% vs. our 42%). **Conclusion: this is structural, not fixable by recalibrating.** The current model is built purely from scoring margin (points scored/allowed), which is well-suited to predicting *how close* a game will be (hence spread's real edge) but not *who wins outright* — a market that's extremely efficiently priced and increasingly informed by things a scoring-margin model can't see (injuries, backup QBs, situational/clutch tendencies) as the season goes on. Keep moneyline disabled; do not spend time re-threshold-sweeping the existing power-rating model for moneyline again. See "New moneyline model" below for the actual next step Bradley chose.

**New moneyline model — built, backtested, and shipped live (2026-08-30).** Built a separate Elo rating (`model/nfl_elo_ratings.py`) — updates from actual win/loss outcomes with margin-of-victory dampening, not raw scoring differential, the same general approach FiveThirtyEight's well-documented NFL Elo used. Also built an optional QB-value adjustment layer (`model/qb_adjustment.py`, using each game's actual designated starter from `schedules_df`'s own `home_qb_id`/`away_qb_id` and their trailing EPA/dropback vs. their own team's recent norm).

Backtested against real 2014-2024 moneylines (1999-2013 burn-in, see `backtest/run_elo_backtest.py`): sanity-checked first (post-2024 ratings correctly ranked Philadelphia/Baltimore/Buffalo/Kansas City/Detroit at the top and Carolina/Tennessee/NY Giants at the bottom — not a buggy model). Results: a modest, real accuracy improvement over the old scoring-margin model (Brier 0.221 vs. 0.228) but still clearly behind the market (0.212), and the QB adjustment layer added **no measurable value at any strength tested** (0/15/25/40 all statistically indistinguishable) — likely because the market prices "who's starting at QB" too fast for a trailing-stat-based signal to get ahead of it. The same red flag from the old model shows up again: win rate on the model's picks gets *less* reliable, not more, at the biggest disagreements (low 40s throughout, collapsing toward 0-20% past edge~0.34, thin samples).

**Shipped live anyway per Bradley's explicit choice (2026-08-30)** — same pattern as NFL/CFB totals, NHL puck line, and MLB moneyline/total: kept visible despite not clearing this project's usual proven-edge bar, clearly labeled speculative. Live threshold `ELO_MONEYLINE_EDGE_THRESHOLD = 0.15` (345 bets, 41.4% win rate, +3.6% ROI in the backtest). The QB adjustment layer is **not** wired into the live pipeline — since it added zero backtested value, it wasn't worth solving the live "who's actually starting this week" confirmation problem (schedules_df's QB id columns are only populated after a game is played, so a live version would need something like NHL's saves-prop confirmation trick). Team-only Elo is used live; can revisit the QB layer if a future backtest with a different design shows real value.

**A real bug was found and fixed while wiring this up (2026-08-30):** `screener/pipeline.py`'s `average_price` was averaging moneyline prices across bookmakers in raw American-odds space. That's fine when every book agrees on the favorite, but breaks down for a near-even game where books disagree (e.g. one book has a team at -105, another at +100) — plain-averaging the raw numbers in that case produces a meaningless blended price. Caught via live testing: a real Week 1 Packers-Vikings line produced a fabricated ~27-point fake edge that disappeared (correctly, down to a real ~10-point edge below the live threshold) once fixed. Fixed by averaging in implied-probability space instead (`model/power_ratings.py`'s new `implied_prob_to_american_odds`). This helper is shared by NHL and MLB's live moneyline screening too, so the fix applies there as well — their backtests are unaffected (those use real historical odds columns directly, not this live per-bookmaker averaging path), but their live picks going forward benefit from the same correction.

## Tech Stack
- Language: Python 3 (via Anaconda)
- Team/Player Stats: `nfl_data_py` (free, pulls from the nflverse/nflfastR data project)
- Play-by-Play Data: `nfl_data_py`'s play-by-play feed (`screener/fetch_pbp.py`) — includes defensive coverage scheme (man/zone) charting, which powers the coverage model
- Betting Odds: The Odds API. **Bet365 is not available through this provider** (confirmed live against every region it supports — us, us2, uk, eu — Bet365 has essentially no US market presence) — Bradley chose to keep the existing cross-book average (DraftKings, FanDuel, BetMGM, BetRivers, Bovada, ESPN Bet, Hard Rock Bet, BetUS, Bally Bet, betPARX, BetOnline.ag, MyBookie.ag) rather than pin to one book or switch providers. **Free tier (500 credits/month) is too small now that the screener runs daily and player props cost one call per game** — Bradley agreed to upgrade to the $30/month/20,000-credit tier; this is a manual step only he can do (needs his payment details) and hadn't been confirmed done as of 2026-08-16.
- Storage: SQLite — `data/cache.db` (TTL cache, gitignored, rebuilt as needed) and `data/ledger.db` (permanent picks ledger, deliberately NOT gitignored — see below)
- Email: SMTP via Gmail
- Scheduling: GitHub Actions (`.github/workflows/screener.yml`), daily at 13:00 UTC (9AM ET). Requires repo secrets `ODDS_API_KEY`, `GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (already set on the GitHub repo as of 2026-08-16)
- Dashboard hosting: GitHub Pages, serving `/docs` on the `main` branch (enabled 2026-08-16)

## Screening Criteria

**Player Props (trend model):**
- Player's opponent-adjusted average over their last 8 games (or fewer, down to 1, with a proportionally bigger required edge)
- Opposing defense's opponent-adjusted allowed average in that stat category
- Flag when the sportsbook line sits meaningfully below (or above, for unders) what both signals support
- True rookie debuts (zero NFL games) are listed separately as "no data yet," not flagged

**Player Props (coverage model, receptions/receiving yards only):**
- Player's opponent-adjusted target volume trend, times a blended zone/man efficiency modifier weighted by this week's opponent's actual coverage mix
- Flag when the resulting predicted value sits 20%+ from the sportsbook line
- Runs alongside the trend model above, not merged with it — shown in its own report section

**Sides / Totals / Moneylines (power-rating model):**
- Team power ratings from scoring efficiency, yardage efficiency, turnover margin, and recent form
- Predicted spread/total generated per matchup
- Flag when market line disagrees meaningfully with our predicted number

## Email Delivery
- **To:** bradleyford5@hotmail.com
- **Schedule:** Daily at 13:00 UTC (9AM ET) via GitHub Actions (`.github/workflows/screener.yml`) — changed 2026-08-16 from the original Mon/Wed/Fri/Sat plan, since odds shift throughout the week and Bradley wants to check the dashboard for updates any day
- **Format:** Ranked pick list, split by strategy (Player Props: trend / speculative / coverage — Sides & Totals). Every email links the live dashboard at top and bottom, including on a "no picks" run
  - Each pick: plain-English explanation of why it was flagged

## Key Directories
- `data/` — `cache.db` (TTL cache, gitignored) and `ledger.db` (permanent picks ledger, tracked in git — see below)
- `model/` — the prediction strategies
  - `model/player_trends.py` — player-vs-defense trend comparison
  - `model/coverage_sim.py` — coverage (zone/man) model for receptions/receiving yards; the live `screen_simplified_coverage_prop` plus the rejected full-simulation attempt, kept for reference
  - `model/power_ratings.py` — team power-rating predictive model (NFL spread/total; moneyline disabled, see above)
  - `model/nfl_elo_ratings.py` — win/loss-focused Elo rating, NFL moneyline only (live, labeled speculative)
  - `model/qb_adjustment.py` — QB-value adjustment layer for the Elo model; built and backtested but not wired into the live pipeline (added no measurable value)
- `screener/` — data fetching, orchestration, scoring
  - `screener/fetch_stats.py` — pulls team/player stats via `nfl_data_py`
  - `screener/fetch_pbp.py` — pulls play-by-play data (coverage scheme, play type) via `nfl_data_py`
  - `screener/fetch_odds.py` — pulls odds via The Odds API
  - `screener/pipeline.py` — runs all models, combines results
  - `screener/scoring.py` — ranks flagged bets
  - `screener/ledger.py` — permanent SQLite store of every pick ever flagged, upserted per (strategy, season, week, subject, market)
  - `screener/reconcile.py` — grades open ledger picks against real results (schedules_df for games, weekly_df for props) once available; reuses the same win/push/loss logic proven in the backtests
- `dashboard/` — generates the interactive dashboard: `build_data.py` shapes results + ledger into the JSON payload, `template.html` + `generate.py` render the self-contained HTML page (no build step, no external dependencies)
- `docs/` — **not** decision notes — this is the GitHub Pages output folder (`index.html` is the live dashboard, regenerated by every full `main.py` run and committed back by the Actions workflow)
- `backtest/` — walk-forward backtests against past seasons: `run_backtest.py`/`simulate.py` for the game model (real historical lines, grades actual ROI), `run_props_backtest.py`/`simulate_props.py` for the trend props model, `simulate_coverage_v2.py` for the coverage model (synthetic line only — see caveat above)
- `email_report/` — email formatting and delivery
- `scheduler/` — unused now that GitHub Actions handles scheduling; kept empty in case that ever changes
- `.claude/memory/sessions/` — session history

## Commands
- Run screener manually: `python main.py`
- Run screener and send email: `python main.py --send`
- Run props only: `python main.py --props-only`
- Run sides/totals only: `python main.py --games-only`
- Test email without running screener: `python -m email_report.send --test`
- Backtest the game model against past seasons: `python -m backtest.run_backtest`
- Backtest the props model (synthetic line only): `python -m backtest.run_props_backtest`

## Conventions
- Use descriptive variable names — no single-letter variables
- Every function needs a one-line plain-English comment explaining what it does
- Log errors clearly so a non-technical user can understand what went wrong
- Never hardcode API keys — always use environment variables or a `.env` file

## Hard Rules
- NEVER commit `.env` files or API keys to git — secrets live in GitHub repo secrets for the Actions workflow, and local `.env` for manual runs
- NEVER send email to any address other than bradleyford5@hotmail.com without explicit approval
- NEVER delete cached data without confirming with Bradley first
- ALWAYS test email formatting before enabling scheduled sends
- This screener produces informational picks only — it never places bets or touches any sportsbook account
- The GitHub repo is public (needed for free Pages hosting) — don't commit anything sensitive beyond what's already there; `.env` is gitignored and was never committed

## Current Work Context
**Status:** Fully automated and live across four sports as of 2026-08-24 — NFL, CFB, NHL, and MLB. Runs daily via GitHub Actions (repo: bradleyford06-ops/nfl-betting-screener), emails standouts to bradleyford5@hotmail.com, and publishes an interactive dashboard to GitHub Pages (https://bradleyford06-ops.github.io/nfl-betting-screener/) with a tab per sport, drill into any game for its picks, season performance broken down by strategy (sport + bet type) on its own tab. NFL/CFB/MLB run on the fixed 9am ET (6am PT) daily schedule; NHL runs on its own dynamic schedule (`.github/workflows/nhl_screener.yml`, polls every 30 min, actually screens ~1 hour before that day's first NHL game) since NHL start times vary too much for a fixed time to work. Don't assume "built" means "working" for this pipeline — every model here has had at least one real bug caught only by testing against live data or a real triggered GitHub Actions run; keep verifying that way, not just locally.
**Per-sport model status:**
- **NFL:** spread has real backtested edge. Moneyline: the original spread-based model is disabled (root-caused, not just untested — see the "Moneyline root-cause investigation" note above); replaced with a new Elo-based model, live but labeled speculative (modest accuracy improvement, still behind the market — see "New moneyline model" note above). Total live but speculative (no edge). Props: trend model (all stats) + coverage model (receptions/receiving yards only), both backtested on historical data but not yet validated against real live prop lines.
- **CFB:** spread has real backtested edge (~54%, +3.3% ROI); total live but speculative (no edge). Own opponent-adjusted power rating, validated against SP+.
- **NHL:** moneyline and total have real backtested edge; puck line live but speculative (its backtest number is mostly a structural artifact of hockey scoring, not real edge). Goalie-adjusted using the Odds API's own `player_total_saves` prop as a starter-confirmation signal (no scraping). No player props yet (shots on goal deliberately deferred until closer to/at the season).
- **MLB:** run line has the strongest real backtested edge of any market in this whole project (55-60% win rate, +11-25% ROI depending on threshold, backed by real market prices); moneyline and total live but speculative (no edge). Pitcher- and park-adjusted (park factors computed directly from real MLB scores, no third-party source). No player props (out of scope per Bradley's explicit choice — game markets only for MLB).
**Outstanding, not on us:** Bradley needs to upgrade the Odds API plan himself (payment details required) if NFL/CFB/NHL/MLB combined live usage ever exceeds the current tier — status unconfirmed as of 2026-08-24, not urgent unless usage warnings appear.
**Known gaps:** NHL result reconciliation isn't built yet (nothing to grade until the NHL season starts — deliberately deferred, not an oversight). NHL shots-on-goal props also deferred until closer to the season. MLB reconciliation *was* built (2026-08-24, same day as the model) since MLB games complete daily — first real graded MLB picks should appear after the Aug 25 9am ET run.
**Reliability infrastructure (added 2026-08-24 after the CFB API-key bug ran silently for two days):** a caught, per-sport failure (screening or reconciliation) now sends a distinct `send_partial_failure_alert` email instead of degrading silently — see `email_report/error_alert.py`. Both GitHub Actions workflows now `git pull --rebase` before pushing, since NFL/CFB/MLB (fixed 9am) and NHL (dynamic time) push to the same repo independently and could otherwise collide. The dashboard is built from the permanent ledger's currently-open picks (not a single run's in-memory results) specifically so multiple independently-scheduled runs can't clobber each other's sections — see `dashboard/build_data.py`.
**Scope decisions:** declined additional NFL prop markets and a DFS lineup optimizer (2026-08-21); MLB scope is game markets only, no props, per Bradley's explicit choice (2026-08-24).
**Next step:** check in on real MLB run-line results once the Aug 25 9am ET run reconciles the first real picks. Ask Bradley whether he wants the daily 9am ET (6am PT) run time shifted to better match his own morning — flagged during this session, not yet decided. Longer-term: NHL needs its shots-on-goal props and reconciliation once its season starts; NFL/NHL props still need validation against real live lines once enough of each season has real data.
**Phase:** 3 of 3 so far (NFL+CFB, NHL, MLB) — each shipped following the same process: scope with Bradley, check real data feasibility, build, backtest against real historical data, calibrate thresholds from evidence, integrate as an independent addition, verify via a real triggered GitHub Actions run before considering it done.
