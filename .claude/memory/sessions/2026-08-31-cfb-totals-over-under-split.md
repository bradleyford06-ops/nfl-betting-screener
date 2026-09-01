# Session: CFB Totals Over/Under Split
Date: 2026-08-31
Project: NFL Betting Screener
Goal: Following the CFB totals deep dive (cache-poisoning bug fix) earlier tonight, Bradley asked whether picking only Overs or only Unders shows a real, profitable edge. Wanted the result implemented if real, but with Overs kept visible (not dropped) and labeled speculative, Unders shown as the real signal.
Continuing from: 2026-08-31-cfb-totals-deep-dive.md (same day, same session)

## Work Log
- Split the existing CFB total-edge data (2015-2024, 7,143 games, clean post-bug-fix) by side. Found Over and Under move in opposite directions as edge grows: Over degrades from 49.7% (edge≥0) to 44.8% (edge≥15); Under improves from ~50% to 55%+.
- Validated Under at edge≥10 across two independent 5-year eras (2015-2019, 2020-2024): 55.1%/+5.2% ROI and 55.2%/+5.3% ROI — nearly identical, one of the cleanest two-era matches found all session. Re-confirmed on the standard 2019-2024 protocol: 56.4%/+7.6% ROI (n=220 on that narrower window).
- Confirmed via the actual `backtest/run_cfb_backtest.py` CLI after implementing (not just a scratch script): Under≥10 gives 225 bets, 56.0% win rate, +6.9% ROI; Over shows the same clean monotonic decay (49.7% down to 40.0% as edge climbs to 20+).
- **Implemented per Bradley's explicit request**: keep Over visible but speculative-only (unchanged flat `TOTAL_EDGE_THRESHOLD = 6.0`), promote Under to a real signal at a new `TOTAL_UNDER_EDGE_THRESHOLD = 10.0`. `screen_cfb_total` now returns a `speculative` boolean (`True` for Over, `False` for Under) that the pipeline uses to route Under into the main results (alongside spread) and Over into the existing speculative bucket — same idiom `model/player_trends.py`'s QB-rushing speculative flag already uses.
- Updated the backtest harness (`backtest/simulate_cfb.py`, `backtest/run_cfb_backtest.py`) to report Under/Over separately so this stays re-validatable with the standard tool.
- Live-tested against real 2026 week 1 CFB data: 6 real Under picks landed in the main flags list, 11 Over picks in speculative — routing confirmed correct.
- **Found a real, if minor and self-resolving, transition side effect while dashboard-testing**: today's earlier production run (which used the pre-fix code, since it ran before this session's changes) had already logged some Under total picks under the OLD `cfb_total_speculative` strategy name. Since the new code logs fresh Under picks under a different name (`cfb_total`), the same game will briefly show what looks like a duplicate pick — one tagged speculative (stale), one not (fresh) — until either the game is played and reconciled (which happens naturally within days for week 1) or the old row ages out of the 72-hour freshness window, whichever comes first. Deliberately did NOT touch the ledger to force-fix this immediately — modifying the permanent ledger without Bradley's sign-off isn't something to do silently, and the issue is minor and short-lived on its own. Flagged clearly to Bradley instead of silently shipping it unexplained.
- Updated all user-facing copy that described CFB totals as uniformly speculative: dashboard CFB tab subhead, `STRATEGY_LABELS` (`cfb_total: "CFB Total (Under)"`, `cfb_total_speculative: "CFB Total (Over, Speculative)"`), email formatter's CFB section (split into "SPREADS & TOTAL UNDERS" main section and "TOTAL OVERS (SPECULATIVE)" section). Also cleaned up NHL/MLB subheads that referenced "same speculative treatment as the CFB total model" — no longer accurate now that CFB total is split, reworded to stand alone.

## Files changed
- `model/cfb_power_ratings.py` — `TOTAL_UNDER_EDGE_THRESHOLD`, `screen_cfb_total` now asymmetric by side with a `speculative` field, full comment documenting the finding
- `backtest/simulate_cfb.py`, `backtest/run_cfb_backtest.py` — Over/Under reported separately
- `screener/pipeline.py` — routes CFB total flags by `speculative` instead of always to the speculative bucket; updated `CFB_TOTALS_ENABLED` and `run_cfb_game_screener` docstrings
- `dashboard/template.html` — CFB tab subhead, `STRATEGY_LABELS`, and NHL/MLB subheads that referenced the old CFB-total-as-speculative comparison
- `email_report/formatter.py` — CFB section split into real (spread + Under) and speculative (Over) parts

## Decisions made
- CFB total: Over kept live and visible per Bradley's explicit request (not dropped, despite no edge), labeled speculative; Under promoted to a real, two-era-validated signal at edge≥10
- Did not attempt to migrate or clean up the pre-existing stale `cfb_total_speculative` Under rows in the live ledger — a temporary, self-resolving display artifact, not worth touching the permanent ledger without Bradley's explicit sign-off for a cosmetic issue that clears itself within days

## Next Step
Report findings to Bradley, including the transition-duplicate heads-up, and confirm whether to push tonight's combined work (CFB cache-poisoning fix + Over/Under split) to GitHub.
