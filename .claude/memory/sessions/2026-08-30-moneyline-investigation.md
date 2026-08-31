# Session: NFL Moneyline Root-Cause Investigation
Date: 2026-08-30
Project: NFL Betting Screener
Goal: Bradley asked us to dig into *why* the NFL moneyline model shows no backtested edge (it's been disabled since the original 2019-2024 backtest, but that backtest only established "no threshold works," not "why").
Continuing from: 2026-08-20-cfb-model.md

## Status at Start
- Last completed: CFB model shipped, NHL model shipped, MLB model shipped, full pipeline reliability audit — all four sports live and automated as of 2026-08-24.
- Pending: real MLB run-line results check-in, whether Bradley shifted the daily run time, Odds API plan upgrade status.
- Blockers: None

## Work Log
- Re-ran `python -m backtest.run_backtest --sweep` (in the `nflbetting` conda env — this project needs that env activated, not base/anaconda default, for `dotenv`/`nfl_data_py` etc.) against real 2019-2024 data. Confirmed moneyline is upside-down: 42-45% win rate at low edge thresholds, and it does NOT improve at higher thresholds the way spread does — it gets *worse* (43.9%→44.0%→33.3% as edge rises from 0.20→0.25→0.30).
- Built a standalone calibration diagnostic (not committed — scratch analysis) comparing the model's predicted home win probability against the sportsbook's own (devigged) implied probability, bucketed against real outcomes:
  - Brier score (accuracy score, lower=better): model 0.228 vs. market 0.210 — market is simply sharper at picking winners.
  - Rebuilt the spread→win-probability conversion from scratch using a real logistic regression fit on actual outcomes, instead of the current fixed-std-dev (13.5) normal-curve approximation. Brier barely moved (0.226) — ruled out "it's just a rough calibration approximation" as the cause.
  - The real tell: win rate on picks *against* the market gets worse as disagreement gets bigger (44%→34%→27%). Real noise (no signal) would hover near 50% regardless of confidence — this pattern means the model is confidently wrong, not randomly wrong.
- Traced the confidently-wrong pattern to time-in-season: bucketed by how many current-season games had been played by both teams. Week 1 (no current-season data): model and market pick winners at similar rates (53% vs 61%). By 9+ games into the season: market picks win 70.5%, model picks win only 41.5% — the gap *widens* through the season instead of narrowing as more real data comes in for both sides.
- **Conclusion: structural, not a bug.** The power-rating model is built purely from points scored/allowed. That's a good proxy for predicting scoring margin (why spread has real edge), but predicting who wins outright is one of the most efficiently-priced markets in sports betting, and the market is clearly incorporating something (injuries, backup QBs, situational/clutch tendencies, coaching adjustments) that pure box-score point-differential can't see — and it does so increasingly well as the season progresses and more of that information becomes available/priced in.
- Reported findings to Bradley in plain English (Brier scores translated as "an accuracy score," logistic regression translated as "rebuilt the win-probability math properly"). Bradley's decision: (1) document this so it isn't re-investigated from scratch later, (2) confirm NFL moneyline never leaks into the live dashboard, (3) scope a brand-new model built specifically for win/loss prediction, not a patched version of the spread model.
- Documented findings in `CLAUDE.md` under the "Sides, Totals & Moneylines" section (new "Moneyline root-cause investigation" note) and in "Current Work Context" per-sport status.
- Kicked off a read-only audit (background agent) of `screener/pipeline.py`, `screener/ledger.py`, `dashboard/build_data.py`, and the live `data/ledger.db`/`docs/index.html` to confirm no stale NFL moneyline rows exist anywhere that could still render on the dashboard even with screening disabled. [Result pending as this note is written — update once back.]

## Next Step (agreed with Bradley) — completed same session
Scoped, built, backtested, and shipped a brand-new NFL moneyline model, not a recalibration of the existing spread-based one.

Built `model/nfl_elo_ratings.py` (Elo rating: updates from actual win/loss outcomes with margin-of-victory dampening, same general approach FiveThirtyEight used for NFL) and `model/qb_adjustment.py` (QB-value adjustment layer using each game's real designated starter from `schedules_df`'s `home_qb_id`/`away_qb_id`, joined to trailing EPA/dropback from weekly stats). Sanity-checked the engine before trusting it (post-2024 ratings correctly topped by Philadelphia/Baltimore/Buffalo/Kansas City/Detroit, bottomed by Carolina/Tennessee/NY Giants).

Backtested against real 2014-2024 moneylines (1999-2013 burn-in, `backtest/run_elo_backtest.py`): modest real accuracy improvement over the old model (Brier 0.221 vs 0.228) but still behind the market (0.212); QB adjustment layer added **zero** measurable value at any strength tested (0/15/25/40 all statistically indistinguishable) — likely because the market prices "who's starting at QB" faster than a trailing-stat signal can get ahead of it. Same red flag as the old model persists: picks get less reliable, not more, at the biggest disagreements.

Recommended NOT shipping given the evidence, but **Bradley explicitly chose to ship it anyway** — same pattern as NFL/CFB totals, NHL puck line, MLB moneyline/total in this project (kept live despite not clearing the proven-edge bar, clearly labeled speculative). Wired into the live pipeline (`screener/pipeline.py`'s `run_game_screener`, new `games_moneyline_speculative` results key, own ledger strategy `moneyline_speculative`, own dashboard badge via `NFL_SPECULATIVE_GAME_STRATEGIES` in `dashboard/build_data.py`, own email section). QB layer deliberately NOT wired live — zero backtested value wasn't worth solving the live "confirmed starter" problem (schedule's QB-id columns only populate after a game is played).

**Found and fixed a real bug during live end-to-end testing** (before touching the live ledger/dashboard — tested via a scratch copy of `ledger.db`, same caution as the CFB build): `average_price` in `screener/pipeline.py` averaged moneyline odds across bookmakers in raw American-odds space, which silently breaks when books disagree on the favorite in a near-even game (one book at -105, another at +100). A real Week 1 Packers-Vikings line produced a fabricated ~27-point fake edge that vanished (correctly, down to a real ~10-point edge below threshold) once fixed to average in implied-probability space instead. This helper is shared by NHL/MLB's live moneyline screening too — their backtests are unaffected (real historical odds columns, not this live path), but their live picks benefit from the same fix going forward.

## Files changed
- `CLAUDE.md` — moneyline root-cause investigation findings, new-model build/backtest/ship writeup, updated NFL/key-directories status
- `.claude/memory/sessions/2026-08-30-moneyline-investigation.md` — this file
- `model/nfl_elo_ratings.py` — new Elo rating engine + `screen_elo_moneyline`
- `model/qb_adjustment.py` — new QB-value adjustment layer (built, backtested, not wired live)
- `model/power_ratings.py` — added `implied_prob_to_american_odds` (the odds-averaging bug fix)
- `backtest/simulate_elo.py`, `backtest/run_elo_backtest.py` — new Elo backtest harness
- `screener/pipeline.py` — wired Elo moneyline into `run_game_screener`/`run_screener`/`log_results_to_ledger`; fixed `average_price`
- `dashboard/build_data.py` — new `NFL_SPECULATIVE_GAME_STRATEGIES`, speculative tagging on NFL game flags
- `dashboard/template.html` — new NFL tab subhead, `moneyline_speculative` strategy label
- `email_report/formatter.py` — new NFL moneyline (speculative) email section
- Dashboard/ledger audit (background agent) confirmed no stale moneyline rows existed anywhere before this session started — nothing needed cleaning up

## Decisions made
- NFL moneyline's original disable is now backed by a root-cause investigation, not just an unexplained backtest failure
- Do not re-attempt to fix the OLD power-rating model for moneyline by recalibrating — ruled out by direct test
- Built a genuinely new, win/loss-focused Elo model — real backtest evidence, shipped live labeled speculative per Bradley's explicit choice despite the investigator's (my) recommendation not to
- QB adjustment layer built and backtested but deliberately not wired into the live pipeline — no measurable value, not worth the added complexity of a live starter-confirmation problem
- Fixed a real, previously-undetected live odds-averaging bug shared by NFL/NHL/MLB moneyline screening, found only through live testing (not caught by any backtest, since backtests use a different, real-historical-odds code path)
