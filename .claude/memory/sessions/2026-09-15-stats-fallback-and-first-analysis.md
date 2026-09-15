# Session: Post-Weekend Review, Stats Fallback, and First Performance Analysis
Date: 2026-09-15
Project: NFL Betting Screener
Goal: Bradley asked for a post-weekend review to confirm the automated runs and dashboard were correct after NFL/CFB Week 1. That review surfaced real players (DJ Moore, Joshua Palmer) mislabeled as rookies, which led to discovering nflverse's weekly player-stats feed had silently stalled after the 2024 season — a much bigger problem than expected. Fixed the whole chain, then ran the season's first real performance analysis once enough picks were finally gradable.

## Work Log

**Name-matching bug (fixed, pushed).** `model/player_trends.py`'s `has_nfl_history`/`player_current_team` did exact string matching between the odds provider's player names and nflverse's `player_display_name` — different spelling conventions (periods in initials: "DJ Moore" vs "D.J. Moore"; nicknames: "Joshua Palmer" vs "Josh Palmer") silently routed real veterans into the "no data yet" rookie list instead of screening them. Replaced with `resolve_player_display_name` — normalized-punctuation match, then a nickname-aware first+last name match. Verified against all 612 real 2024 players with zero false collisions. Wired into `screener/pipeline.py` (both prop screeners) and `screener/reconcile.py` (grading).

**nflverse's weekly player-stats release has stalled since May 2025.** Checked the real GitHub release directly — every player-stats file caps at 2024, last updated 2025-05-07. This affected every prop pick all season (trailing averages built on 2024 data) and blocked all prop reconciliation (nothing to grade against). Real-world lag, not our bug.

**Stats fallback, iterated twice.** First built a fallback onto NFL's Next Gen Stats (also nflverse-distributed, confirmed current). Found live gaps: NGS's receiving file excludes RBs, its rushing file excludes QBs. Rebuilt instead directly from play-by-play (`screener/fetch_stats.py`'s `_weekly_stats_from_pbp`) — raw plays are tagged by the actual player regardless of position, so no positional gap. Verified exact match against NGS's own passing numbers, and found PBP is actually *more* complete than NGS's own weekly file (NGS silently drops some player-weeks its own season-to-date total doesn't reflect — confirmed for both Lamar Jackson and Saquon Barkley).

**Second real bug, found via Bradley's own dashboard check**: Erick All / Calvin Ridley props from a completed Week 1 game still showed "open" days later. Root cause: the PBP rebuild only creates a row when a player records a qualifying play (target/carry/attempt) — a player on the field with a genuine zero-target game had no row at all, so reconcile.py could never grade the "obviously lost" pick. Fixed with `_active_skill_players` — cross-references nflverse's snap-count data (via its own PFR-id↔GSIS-id crosswalk) to add an explicit zero row for anyone confirmed to have played. Verified: both picks now grade correctly.

**Third bug, found while verifying the second fix**: `actual_value` for all 265 props reconciled today was silently stored as a corrupted SQLite BLOB instead of a REAL number — `numpy.float32` (from `downcast=True`) has no sqlite3 adapter. Never affected grading (the comparison happens before storage) and wasn't displayed anywhere yet, but was unusable by anything reading it back. Fixed in `screener/reconcile.py` (cast to `float()`), and repaired all 265 already-corrupted rows by decoding the still-intact bytes back to their real values.

**Added structured predicted-value tracking, all sports.** Bradley wants to analyze results in depth over time — win/loss alone doesn't answer "how far off was the model." Added `predicted_value` + `predicted_value_type` columns to the ledger; `screener/pipeline.py`'s `log_results_to_ledger` picks whichever of the model's own field names applies (`predicted_spread`, `predicted_total`, `model_win_prob`, `model_cover_prob`, `player_recent_avg`, `predicted_value`) per strategy — one small lookup covers every sport and market this project screens. Only applies going forward (no way to recover a number never captured for older picks).

**Verification discipline**: pushed each fix individually, then triggered a real `gh workflow run screener.yml` and watched it complete before moving to the next fix — same "verify against a real triggered run" standard the project has followed all along. Four manual runs total this session.

**First real performance analysis, NFL props Week 1** (`analysis/props_performance.py`, artifact "First Read"): overall 49.4% (n=265) looks like a coin flip, but WR props are a real, statistically significant drag — 32.9% (n=85, 40 distinct players) vs. 57.2% for every other position combined (p=0.0002). Shows up in both the trend and coverage models. WR Overs miss their line by ~5.9 units on average; every other position's Overs beat their line on average. TE (63.0%) and the Over/Under gap (46.1%/58.1%) both looked interesting but weren't statistically significant on their own — TE's strength is concentrated in a few players going 4-for-4 on correlated picks, and the Over/Under gap mostly *is* the WR finding showing up a second way (non-WR Overs alone: 54.5%).

**Second analysis, CFB spread Weeks 1-2** (`analysis/cfb_performance.py`, artifact "Second Read"): live 42.9% (n=63) looks alarming next to the documented 54.1% backtest, but re-running the actual backtest broken into individual weekly samples (not just the pooled 6-year average) showed 30% of historical weeks performed this badly or worse purely from normal variance — the live shortfall isn't statistically distinguishable from noise yet (p=0.073). Two things worth watching, not yet acting on: our predicted margins run consistently more extreme than the market's on games we bet (partly expected, since we only bet where we disagree — but a few of the worst misses, e.g. Louisiana Tech @ LSU, Texas State @ Texas, share a "market smelled a bigger blowout than we did" pattern); and 3 of 64 picks touched the already-documented, deliberately-unfixed FCS-transition-program gap (Kennesaw State, James Madison, Sam Houston). CFB totals are tracking their backtested expectations closely (real Under: 55.6% vs. documented 55-56%; speculative Over: 36.4%, expected to be weak).

**Built reusable analysis tooling** (`analysis/`) instead of leaving this as one-off queries — `props_performance.py` and `cfb_performance.py`, both verified against the real ledger to reproduce the exact numbers from the two reviews above. See `analysis/README.md`.

## Files changed
- `model/player_trends.py` — `resolve_player_display_name` (name-matching fix), `pd.isna` guard fix for a NaN-vs-None edge case the PBP fallback newly exposed
- `screener/pipeline.py` — wired the name resolver into both prop screeners; `predicted_value`/`predicted_value_type` extraction in `log_results_to_ledger`; same `pd.isna` guard fix in the coverage screener
- `screener/reconcile.py` — name resolver in prop grading; `float()` cast fixing the `actual_value` blob bug
- `screener/fetch_stats.py` — `_weekly_stats_from_pbp` (the play-by-play stats fallback, replacing an earlier NGS-based version), `_active_skill_players` (the zero-stat-game fix)
- `screener/ledger.py` — `predicted_value`/`predicted_value_type` columns and migration
- `data/ledger.db` — repaired 265 corrupted `actual_value` rows
- `analysis/` — new: `props_performance.py`, `cfb_performance.py`, `README.md`
- `CLAUDE.md` — pointer to `analysis/`

## Decisions made
- Chose play-by-play over Next Gen Stats as the primary stats fallback after live-testing exposed NGS's own positional and completeness gaps — this was a mid-session pivot, not the original plan
- Didn't attempt to backfill `predicted_value` for picks made before today — genuinely unrecoverable, not worth building a parser for every historical strategy's explanation text when `cfb_performance.attach_predicted_margins` already demonstrates that's possible case-by-case if ever needed
- Treated the CFB spread shortfall as "not yet distinguishable from noise" rather than a problem to fix, based on the backtest's own weekly variance — explicitly did not touch any live threshold
- Built `analysis/` as reusable modules (not just chat-only queries), per Bradley's request to keep doing this over time

## Next Step
Re-run both analyses in 3-4 weeks with more data — the NFL WR finding has enough evidence to trust already; CFB spread needs a bigger sample before concluding anything. Once there's real post-2026-09-15 predicted_value data, prefer it over `cfb_performance.py`'s text-parsing fallback. Also worth eventually checking: the "our margins run hot vs. market on lopsided games" CFB observation, if it keeps recurring.

## End of Session

Completed this session:
- Confirmed the weekend's automated runs and dashboard were working correctly
- Found and fixed a bug causing real players (like DJ Moore) to show up as fake "rookies" with no data
- Discovered our free stats source had silently stopped updating player stats back in May 2025 — a much bigger problem than it first looked like
- Rebuilt how we pull player stats (now sourced directly from play-by-play data) so it's current again and doesn't have the gaps an earlier attempt had
- Fixed two more bugs found along the way: props that could never be graded when a player had a truly quiet game, and a technical storage bug that was silently corrupting how results were saved (repaired all 265 affected records)
- Every pick, across all four sports, now saves the model's own predicted number — not just win/loss — so we can analyze results in real depth going forward
- Ran the full pipeline live several times and confirmed each fix actually worked before moving on
- Did a full analysis of Week 1 NFL player props — found a real, statistically meaningful weak spot in receiver props specifically
- Did a full analysis of CFB spread picks — the early results that looked concerning turned out to be normal ups-and-downs, not a broken model, once checked against historical patterns
- Built reusable analysis tools we can just re-run in a few weeks with more data, instead of starting from scratch each time
- Published two written reports (First Read, Second Read) with the findings

Still pending:
- Re-run both analyses in 3-4 weeks once more games are in the books
- Keep an eye on whether the CFB "predicted margin runs hotter than the market" pattern shows up again

Files changed:
- `model/player_trends.py` — added the name-matching fix (`resolve_player_display_name`) and a NaN-safety fix
- `screener/pipeline.py` — wired the name fix into both prop screeners; added predicted-value tracking; same NaN-safety fix
- `screener/reconcile.py` — wired the name fix into grading; fixed the corrupted-storage bug
- `screener/fetch_stats.py` — rebuilt the player-stats fallback on play-by-play data, including the fix for true zero-stat games
- `screener/ledger.py` — added the predicted_value/predicted_value_type columns
- `data/ledger.db` — repaired 265 corrupted result records
- `analysis/` — new folder with reusable analysis tools (`props_performance.py`, `cfb_performance.py`, `README.md`)
- `CLAUDE.md` — pointer to the new analysis folder

Decisions made:
- Chose the play-by-play rebuild over the earlier Next Gen Stats version once live testing showed it was both more complete and gap-free
- Didn't try to recover the model's predicted numbers for picks made before today — not possible to do reliably, and the tracking is in place going forward
- Treated the CFB spread shortfall as normal variance, not a problem — explicitly did not change any live settings based on it
- Built the analysis folder as real, reusable code (not just one-off chat answers), since Bradley wants to keep doing this over time

Blockers or warnings:
- None active — everything is running cleanly. The original free stats source is still down upstream as of today, but that no longer affects us since the play-by-play fallback covers it.

Recommended first step next session:
Re-run `analysis/props_performance.py` and `analysis/cfb_performance.py` once a few more weeks of picks have graded (roughly 3-4 weeks from today) to get a statistically meaningful read on both the WR props finding and the CFB spread question.

Session duration: long session (multiple hours) — covered a full post-weekend review, three chained bug fixes, a new tracking feature, and two full performance analyses with published reports.
