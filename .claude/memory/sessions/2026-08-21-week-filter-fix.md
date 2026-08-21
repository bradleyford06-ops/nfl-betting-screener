# Session: Week Filter Fix
Date: 2026-08-21
Project: NFL Betting Screener
Goal: Fix a bug where the email/dashboard was flagging edges across the whole season instead of just the upcoming week, then review prop coverage, a DFS idea, and performance tracking.
Continuing from: 2026-08-17-odds-api-bet365.md

## Status at Start
- Last completed: Full pipeline built, backtested, and automated end-to-end (daily GitHub Actions run, email, dashboard, ledger) — confirmed live as of 2026-08-18. Bradley decided to stick with The Odds API for now and upgrade to the paid tier right before the season starts, rather than immediately.
- Pending: Verify both props models against real live prop lines once posted; watch QB-rushing speculative picks over the season; Odds API upgrade deliberately deferred to closer to the season
- Blockers: None

## Work Log
- Bradley reported that email notifications were showing edges for the whole season, not just the upcoming week (week 1)
- Diagnosed: `get_game_odds()`/`get_events()` return the entire remaining season's games in one call (~272 events), and none of the three screener functions (`run_game_screener`, `run_props_screener`, `run_coverage_screener`) filtered by week — every run screened and emailed picks from every future week at once
- Fix: added `get_current_week(schedules_df)` to `screener/pipeline.py`, which finds the nearest week with at least one unplayed game. All three screeners now skip any game whose resolved season/week (via the existing `lookup_season_week`, previously only used for ledger bookkeeping) doesn't match. Added a safety guard: if the current week can't be determined, filtering is skipped entirely (falls back to old full-season behavior) rather than silently producing an empty report
- Side benefit: for the two props screeners, moved the week check to before the per-event props API call rather than after, so this also cuts API usage substantially (previously fetching props for ~272 events every run regardless of week)
- Verified locally: a full run went from 156+ flagged picks down to 9, all confirmed "2026 week 1"
- Hit a git push conflict pushing the fix: the remote had 3 "automated run" commits from real scheduled runs (using the old buggy code) that modified `data/ledger.db` and `docs/index.html`, conflicting with local changes to the same generated files. Resolved by taking origin's version as the base (can't meaningfully hand-merge binary/generated files) and regenerating fresh locally on top of the merged code
- Verified the fix live in production: triggered the actual GitHub Actions workflow, confirmed via logs it screened "2026 week 1" and sent a real email with 9 flagged games — matches local testing exactly
- Also committed a stray session file (`2026-08-17-odds-api-bet365.md`) that existed on disk but was never committed from the prior session
- Reviewed current player-prop market coverage at Bradley's request: currently running 10 markets (passing/rushing/receiving yards, TDs, attempts/completions/receptions). Identified major gaps by checking The Odds API's actual market list: anytime TD scorer (probably the most-bet NFL prop that exists), kicker props (zero coverage), defensive player props (sacks, tackles+assists, INTs — a whole missing category), QB interceptions, and combo/combined yardage props. Noted that extending to these isn't a simple config change — anytime TD needs different (probability-based) modeling logic, and defensive props need a whole new position/stat framework. Bradley decided not to build any of these out for now
- Explored a daily fantasy sports (DFS) lineup-optimizer idea: broke it into three pieces — projections (already have this via the trend model), optimization (a solved problem; verified `pydfs-lineup-optimizer` is a real, free, actively-used open-source Python library built for exactly this), and salary data (the real open question — no official free API from DraftKings/FanDuel; recommended a weekly manual CSV export, the standard sanctioned way DFS players already get this data, over scraping undocumented internal endpoints). Bradley decided not to pursue this for now
- Confirmed and demonstrated that the season-performance dashboard already breaks down win rate/ROI by strategy (spread/total/props trend/speculative/coverage), not just an overall number — this already existed from the earlier dashboard build. Bradley confirmed he's satisfied with the current tracking depth as-is
- Flagged one minor, unaddressed polish item: the season-performance cards currently show raw internal strategy strings (e.g. "props_trend") as labels rather than a formatted human-readable name — noted but not actioned, since Bradley moved to ending the session before addressing it
- Bradley confirmed the ledger already captures everything needed to review performance in later weeks (strategy, season/week, subject, market, side, line, price, edge score, matchup context, timestamps, and final outcome once reconciled) — permanent, append-only, nothing overwritten
- Bradley wants to build a new model/strategy next session, to be added to this same project — not yet scoped or discussed in any detail

## End of Session

Completed this session:
- Fixed a major bug: the screener was flagging edges across the entire season instead of just the upcoming week, because the odds API returns the whole season's games in one call and nothing filtered by week. Added a week-detection function and applied it across all three screeners; also made the props screeners skip the (expensive) props API call for off-week games, cutting API usage substantially as a side benefit
- Verified the fix for real: triggered the actual GitHub Actions workflow, confirmed the real email that went out had only 9 week-1 picks (down from 150+)
- Hit and resolved a git push conflict along the way (automated dashboard/ledger commits from real scheduled runs collided with local changes) — merged cleanly by regenerating the dashboard/ledger fresh rather than hand-merging binary files
- Reviewed current player-prop market coverage (10 markets: passing/rushing/receiving) against what's available (anytime TD scorer, kicker props, defensive props, INTs, combo yardage props) — informational, no changes made
- Explored a daily fantasy lineup-optimizer idea: confirmed it's genuinely feasible (projections already exist, a free open-source optimizer library exists, salary data would come from a manual weekly CSV export since neither DK nor FanDuel has a public API) — Bradley decided not to pursue this for now
- Confirmed the season-performance dashboard already breaks down win rate/ROI by strategy (spread/total/props trend/speculative/coverage), not just an overall number — Bradley is satisfied with the current tracking depth

Still pending:
- Bradley wants to build a new model/strategy next session, to be added to this same project — not yet scoped or defined
- Minor, undecided polish item: the season-performance cards show raw internal strategy names (e.g. "props_trend") instead of readable labels — flagged, not yet actioned or explicitly requested
- Odds API plan upgrade — still deliberately deferred until closer to the season (per the prior session's decision)

Files changed:
- `screener/pipeline.py` — added `get_current_week()`, and week-filtering checks in `run_game_screener`, `run_props_screener`, and `run_coverage_screener`
- `data/ledger.db` — regenerated multiple times as the ledger absorbed real week-1 picks and merged with automated-run history
- `docs/index.html` — regenerated to match, now showing only week-1 picks
- `.claude/memory/sessions/2026-08-17-odds-api-bet365.md` — committed (existed on disk but was never committed last session)

Decisions made:
- Not building a DFS lineup optimizer for now, despite confirming it's technically feasible
- Not expanding to new prop markets (anytime TD, kicker props, defensive props) for now
- Current season-performance breakdown (by strategy) is sufficient as-is, no changes requested
- When a generated/binary file (ledger, dashboard HTML) conflicts across a git merge, resolve by taking one side wholesale and regenerating fresh rather than hand-merging — established as the standard approach for this project

Blockers or warnings:
- None new. Existing ones carry over: Odds API free tier will likely fall short once real prop volume shows up (upgrade deliberately deferred, not forgotten); nfl_data_py's 2025 weekly stats were 404ing as of last check (already handled gracefully).

Recommended first step next session:
Ask Bradley what the new model/strategy idea is, then follow the same process used for every prior model in this project — understand the reasoning together, check real data feasibility before committing, build, backtest against real historical data, calibrate thresholds with evidence, and integrate as an independent addition rather than replacing what already works (mirroring how the coverage model was added as a second signal alongside the trend model, not instead of it).

Session duration: Single session, focused mostly on the week-filter production bug fix plus three shorter discussion topics (prop coverage review, DFS feasibility, performance tracking confirmation).
