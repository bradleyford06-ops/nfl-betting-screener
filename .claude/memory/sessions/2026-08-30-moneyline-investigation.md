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

## Next Step (agreed with Bradley)
Scope a brand-new NFL moneyline model, not a recalibration of the existing spread-based one. Leading candidate discussed: an Elo-style rating (updates from actual win/loss outcomes with margin-of-victory dampening, rather than raw scoring differential) with a QB-value adjustment layer — the same general approach FiveThirtyEight's well-documented NFL Elo used. This directly targets the diagnosed gap (backup-QB/situational swings a scoring-margin model can't see). Needs its own scoping conversation, data-feasibility check, build, and backtest before going live — same process as every other model in this project (see CFB/NHL/MLB sessions for the pattern).

## Files changed
- `CLAUDE.md` — added the moneyline root-cause investigation findings and updated NFL per-sport status
- `.claude/memory/sessions/2026-08-30-moneyline-investigation.md` — this file
- (pending) dashboard/ledger cleanup if the background audit finds stale moneyline rows

## Decisions made
- NFL moneyline stays disabled — now backed by a root-cause investigation, not just an unexplained backtest failure
- Do not re-attempt to fix moneyline by recalibrating or re-threshold-sweeping the existing power-rating model — ruled out by direct test
- Build a genuinely new, win/loss-focused model (Elo-style + QB adjustment) as a separate future project, scoped but not yet started
