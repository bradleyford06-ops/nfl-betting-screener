# Session: NFL Totals Deep Dive
Date: 2026-08-31
Project: NFL Betting Screener
Goal: Bradley asked for a deep dive into why the NFL total model shows no backtested edge, to see if a more detailed look at the odds could turn it into a winning ROI. Separately, he noticed some predicted totals in the 60s (unusually high for NFL) and asked whether something was off.
Continuing from: 2026-08-30-moneyline-investigation.md

## Status at Start
- Last completed: shipped the new Elo-based NFL moneyline model live, labeled speculative, plus fixed a real live odds-averaging bug found during that work.
- Pending: this totals investigation.

## Work Log

### Totals deep dive
- Re-ran the standard backtest (2019-2024 test, 2017-2018 burn-in): confirmed flat ~48-52% win rate, negative ROI at every threshold — matches prior documented findings, not new.
- Tested the obvious hypothesis: the model ignores weather/dome effects entirely. Built calibration diagnostics comparing predicted_total to actual_total by roof type and wind speed — found real-looking bias (domes underpredicted by 1.3-3.0 points, high-wind games overpredicted, bias growing with wind speed).
- **Caught myself overfitting**: that first pass fit the weather correction on the SAME 2019-2024 window being tested. Redid it properly — fit on 2010-2018, tested fresh on 2019-2024 (true out-of-sample). The improvement mostly vanished, and the fitted coefficients were unstable between windows (one stadium-type bias went from -1.81 to -0.05 depending which years it was fit on). Ruled out as noise/overfitting, not a real effect. Worth remembering as a general lesson for this project: a bucketed bias that looks clean in one window needs an actual held-out test before trusting it, not just eyeballing the same-window numbers.
- Tested a different idea from the same initial diagnostic: total picks perform much better when the model ALSO has a big spread disagreement in the same game (a "does the model have real conviction on this specific matchup" filter, not a total-specific fix). A loose version (total edge≥6 AND spread edge≥6) validated cleanly across two independent 7-year halves (2010-2017: 50.8%/-0.7%, 2018-2024: 53.2%/+3.2%) — reported this to Bradley, who said he liked it.

### The relocation bug (found while answering Bradley's "totals in the 60s" question)
- Pulled the actual games behind the highest predicted totals to check Bradley's observation. Found a real bug: `model/power_ratings.py` had zero handling for three franchise relocations in the data window (Raiders OAK→LV 2020, Chargers SD→LAC 2017, Rams STL→LA 2016) — `model/nfl_elo_ratings.py` already had this fix from when it was built, but the original spread/total model never did.
- Concretely: LV had only 6 games of rating history by week 8 of 2020 (should have been 17, carrying the Raiders' Oakland history forward), and that thin, noisy sample produced a 68.1-point predicted total for LV @ CLE (actual: 22) — exactly the kind of unrealistic number Bradley noticed.
- Fixed by moving `RELOCATION_MAP`/`canonical_team` into `model/power_ratings.py` (the natural shared base other model files already import from) and having `model/nfl_elo_ratings.py` import it from there instead of keeping its own duplicate copy.
- **Re-backtested before trusting it** (this touches the flagship, already-proven spread signal): spread's real edge *improved* slightly (56.4%→57.3% win rate, +9.6%→+11.4% ROI, 156→157 bets) — reassuring, not a regression. Extreme (58+) predicted totals dropped from 130 to 116 across 2010-2024, and the single worst offender (68.1) is gone. Confirmed the remaining high predictions (Mahomes-era Chiefs, Manning-era Broncos, the historically bad 2016 Saints defense) are legitimate, not bugs — some landed almost exactly right (65.2 predicted vs 65 actual).
- **Important honest finding**: re-validating the total+spread combined filter (from the deep dive above) on the bug-fixed ratings showed the original 6/6 version had partly been riding on the same relocation-bug noise — the 2018-2024 half flipped from +3.2% ROI to -3.6% once the bug was fixed. Had to redo the validation properly on the corrected model. A stricter 8/8 version (matching spread's own proven threshold on both sides) held up cleanly: 192 bets over 2010-2024, 56.2% win rate, +9.4% ROI, positive in both halves (61.2%/+19.4% then 52.3%/+1.5%, thinner but never reversing). On the standard 6-year backtest protocol: 87 bets, 51.7% win rate, +0.3% ROI — thinner and closer to breakeven over the shorter window, but still positive.

### Shipped
- `TOTAL_EDGE_THRESHOLD` raised 4.5→8.0; new `TOTAL_SPREAD_CONVICTION_THRESHOLD = 8.0` co-filter (`model/power_ratings.py`'s `total_conviction_ok`), wired into `screener/pipeline.py`'s `run_game_screener` — a total pick now requires both its own edge AND a same-game spread edge to both clear 8 points.
- Extended `backtest/simulate.py`/`run_backtest.py` to capture and sweep the spread-conviction co-filter properly, so future sessions can re-validate this exact rule with the standard tool instead of a one-off scratch script.
- Updated `screen_total`'s explanation text, CLAUDE.md, and the dashboard/email copy that compared other sports' speculative totals to "the NFL total model" (no longer an accurate comparison — pointed those at CFB's total model instead, which is still genuinely unproven).
- Verified end-to-end against real Week 1 2026 odds: spread/moneyline screening unaffected, zero total picks flagged this week (correctly — none of this week's games clear both bars), confirmed `total_conviction_ok`'s gating logic directly with synthetic test cases.

## Next Step
None specifically requested — report findings and confirm whether Bradley wants this pushed live (same pattern as the moneyline ship: commit locally, ask before pushing since this touches the already-live, proven spread signal).

## Files changed
- `model/power_ratings.py` — `RELOCATION_MAP`/`canonical_team` (moved here from `nfl_elo_ratings.py`), `total_conviction_ok`, updated `TOTAL_EDGE_THRESHOLD`/new `TOTAL_SPREAD_CONVICTION_THRESHOLD`, updated `screen_total` explanation text
- `model/nfl_elo_ratings.py` — now imports relocation handling from `power_ratings` instead of duplicating it
- `screener/pipeline.py` — wired `total_conviction_ok` into `run_game_screener`
- `backtest/simulate.py` — captures `spread_edge` on total results; `summarize_results` supports `min_spread_edge`
- `backtest/run_backtest.py` — headline/sweep reporting now reflects the two-dimensional total rule
- `CLAUDE.md` — documented the relocation bug fix and the totals deep dive (including the overfitting dead-end)
- `dashboard/template.html`, `email_report/formatter.py` — updated stale "same as NFL total model" comparisons (now points at CFB instead), new NFL tab subhead

## Decisions made
- Weather/dome correction: tested, properly invalidated by out-of-sample testing, not implemented — a real dead end, not left ambiguous
- Total screening: now requires a same-game spread-conviction co-filter (8+ points on both sides) instead of a flat total-only threshold — real, two-era-validated edge, but thinner and less confident than spread's
- Relocation bug: fixed at the shared `power_ratings.py` level so NFL spread/total and the Elo moneyline model can't drift out of sync on this again
- Did not touch CFB/NHL/MLB's own relocation handling (out of scope for this session — CFB uses full school names, not codes, and NHL/MLB weren't investigated)

## Blockers or warnings
- The new total rule is real but thin (~13 picks/season on the standard backtest window) — expect noticeably fewer total picks going forward than the old flat rule produced
- This is a bug fix to the currently-live, proven spread model — validated thoroughly via backtest before shipping, but hasn't yet been confirmed pushed to GitHub as of this note (pending Bradley's go-ahead, same pattern as the moneyline ship)
