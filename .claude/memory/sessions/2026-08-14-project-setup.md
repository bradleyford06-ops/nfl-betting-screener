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
- Bradley wanted to work on the player props model next, and explicitly asked for a walkthrough of the reasoning so he could contribute his own betting expertise before I built anything — walked through the full screen_player_prop logic (recent average vs. line, defense allowed vs. league average, same-direction gate, thresholds) and flagged where I'd made judgment calls
- Bradley identified two real gaps: (1) rookies with no NFL history (only college stats) weren't handled — currently just silently skipped, (2) the defense-allowed number and the player's own average both have the same unadjusted-for-schedule bias we just fixed on the game model
- Proposed a plan for both and got explicit sign-off before building: skip true rookie debuts from flagging but surface them in a "no data yet" list rather than vanishing silently (vs. trying to translate college stats — bigger, less reliable build); lower the 3-game minimum sample to 1 game but scale the required edge up for thinner samples (~sqrt(8/n)) instead of a hard cutoff
- Built the opponent-adjustment fix by reusing the game model's iterative rating machinery instead of writing new logic: `screener/fetch_stats.py`'s new `build_position_stat_team_games` reshapes weekly player stats into the same team-vs-team shape `power_ratings.ratings_from_team_games` already expects, just run separately per position/stat combo (e.g. "RB rushing yards") instead of per team score. This fixes the defense side directly (opponent-adjusted allowed rating) and the player side (each past game corrected by how tough that specific opponent was, then averaged) using one underlying system
- Added `has_nfl_history`, rewired `screener/pipeline.py`'s `run_props_screener` to route zero-history players to a separate `no_data` list instead of `continue`-ing past them silently, and added a "PLAYER PROPS — NO DATA YET" section to `email_report/formatter.py`
- Ratings are cached per (position, stat) within a single screener run via a `ratings_cache` dict passed through, since many players share the same combo (e.g. every flagged RB rushing prop reuses the same "RB rushing_yards" ratings) — avoids redundant recomputation
- Sanity-checked against real 2023-2024 weekly data (no live prop lines exist yet to validate against): ratings looked structurally sound (BAL toughest run defense at -28.9, CAR worst at +52.7, no NaNs), threshold scaling behaved correctly (1 game → 22.6% required edge vs. 8.0% at full sample, matching the sqrt(8/n) formula), a real player's raw vs. adjusted average moved in a sane direction, an end-to-end synthetic-line test correctly flagged an Over, and a genuine 1-game edge case (0-yard game) correctly did NOT flag because the defense signal didn't corroborate direction — confirms the double-confirmation gate is working, not just passing through noise
- Full `main.py` smoke test (both games and props) still runs clean end-to-end
- Honest gap acknowledged in CLAUDE.md: unlike the game model, there's no free source of historical player-prop lines, so this fix is reasoned/backtested-by-analogy (reusing math validated on the game model) rather than independently proven — real prop-line testing has to wait for the season
- Bradley then asked to dig into the props edge thresholds specifically (8%/5% flat, applied to every stat category) — asked what my thought process was before building anything, consistent with how he engaged on the earlier walkthrough
- Proposed building a "synthetic-line" backtest since real historical prop lines don't exist for free: use each player's own trailing average as a stand-in market line, walk forward with no lookahead, and check whether the recent-trend + defense signal correctly predicted beating/missing it. Flagged the honest limitation upfront — beating your own long-run average is a much lower bar than beating a real sportsbook line — but it's real evidence of whether the signal has any predictive content, which we had zero of before
- Bradley specifically wanted WR vs. RB receiving yards and QB vs. RB rushing yards compared, suspecting real volatility differences between positions sharing the same stat category
- Built `backtest/simulate_props.py` + `run_props_backtest.py` and ran it against 2019-2024: confirmed his hypothesis with real numbers — RB receiving yards is ~2.6x noisier than WR (259.6% vs 100.5% stdev), QB rushing yards is ~3.7x noisier than RB rushing (505.6% vs 137.5%). Also found the flat 8% threshold was too loose everywhere — every combo needed 12-30% edge before showing a real hit rate above 50% in the sweep
- Bradley's decision: build per-(position,stat) thresholds from the sweep data, and keep QB rushing live (weakest signal, 54.5% best case) but flagged separately since he wants the information to watch for a trend over the season and will make his own call on those bets
- Implemented `POSITION_STAT_THRESHOLDS` in `model/player_trends.py` (calibrated per combo), added a `speculative` flag on QB rushing results, split `run_props_screener` into three lists (flags/speculative_flags/no_data), and added a "PLAYER PROPS — SPECULATIVE" section to the email formatter, clearly labeled as informational not a recommendation
- Verified directly: a WR line with a 12% edge (would've flagged under the old 8% threshold) now correctly returns None under the new 20% WR threshold; a comparable RB rushing edge still flags under RB's looser 12% threshold — confirms the per-position calibration is actually taking effect, not just cosmetic
- Updated CLAUDE.md with the full per-position threshold table and the caveat about what the synthetic-line backtest can/can't prove

## Work Log — 2026-08-16 continuation (coverage model)
- Bradley proposed a much deeper player-props idea: simulate expected plays/pace, split into run/pass, split pass plays by man/zone coverage, then apply a player's specific performance vs. each coverage type — a materially more sophisticated approach than the trend model, closer to how serious DFS/prop models work. Example he gave: a player who averages 3.7 catches/31 yards overall but 5.8/55 specifically vs. zone, matched against an opponent that plays zone 75% of the time
- I initially assumed coverage-scheme data would require paid charting (PFF-style) and said so — was wrong. Checked and found nflverse's free play-by-play feed (already our data source) includes `defense_man_zone_type`/`defense_coverage_type` (actual Cover 0-9 detail), joinable to individual plays via `receiver_player_id` which matches weekly data's `player_id` exactly. 92%+ complete back to 2018, 100% for 2023-2024 — genuinely buildable with what we already had access to, just hadn't looked
- Bradley wanted this built before the season as a deliberate phase-2 upgrade, and specifically asked for small-sample handling to be "highlight, don't hide" (surface sample size, let him judge) rather than statistical smoothing — matches his pattern of wanting visibility over the model making silent judgment calls for him
- Agreed to build it as a fully separate model from the trend model (Bradley's choice), validated independently before deciding on rollout
- Built `screener/fetch_pbp.py` (play-by-play fetch + cache, trimmed to needed columns since nfl_data_py's own column-filtering behaved inconsistently in testing) and `model/coverage_sim.py`'s first version: team pace + pass-rate tendencies, defensive zone-rate tendency, player target share, player coverage-specific efficiency splits, all multiplied together into a full bottom-up simulation. Sanity-checked against a real player (Breece Hall, the same one from Bradley's example) — his real zone/man splits looked exactly like the hypothesized pattern (88% catch rate/7.2 ypt vs zone, 65%/5.0 vs man, solid samples)
- Backtested the full model (`backtest/simulate_coverage.py`) with the same synthetic-line methodology as the trend model. First run: ~51-53% hit rate that got WORSE with bigger edges (46-48%) — even restricted to 50+ target samples, ruling out "just needs a bigger sample" as the explanation. Diagnosed why: multiplying together four independently-estimated numbers (team pace, pass rate, zone rate, target share) compounds their noise instead of averaging it out — a real, generalizable lesson, not specific to this one model
- Hit a genuine performance bug while backtesting: `.set_index()` called inside the innermost per-player-game loop instead of once per player, which combined with something (unclear exactly what, possibly system load) to make one backtest run take 112 minutes instead of the expected ~4. Learned to run long backtests in the background from the start rather than blocking synchronously, and to watch CPU-time-vs-wall-clock ratio to detect genuine hangs vs. just slow days
- Reported the negative full-model result to Bradley plainly; he said the idea still has truth to it but the model was too noisy, and asked what a simplified version would look like
- Proposed and built the fix: keep player volume as the trend model's own already-proven opponent-adjusted average (reusing `player_trends.py`'s `position_stat_ratings`/`player_adjusted_average` machinery directly, on the `targets` stat, which weekly data already has) instead of rebuilding it from a chain of estimates, and apply coverage as a single efficiency modifier on top. This cut 3 of 4 compounding estimates down to one proven one
- Backtested the simplified version (`backtest/simulate_coverage_v2.py`, with the `.set_index()` bug fixed and per-cutoff ratings properly precomputed and reused across players — same efficient pattern as the other backtests): 27 seconds for WR receiving yards alone, clean monotonic hit rate climbing 53.4% → 57.2% as edge threshold rose. Ran across all 6 receiving combos (WR/RB/TE × receptions/receiving yards): every one showed the same real-signal pattern, 53-64% depending on combo, receptions markets stronger than yardage, TE receptions strongest (54.2% → 64.1%, though on the smallest sample of the six)
- Asked Bradley how this should fit with the existing trend model (replace it, require both to agree, or run separately) — he chose to run both separately, clearly labeled, so he can see where they agree or disagree rather than have them merged or gated on each other
- Calibrated `COVERAGE_EDGE_THRESHOLD = 0.20` (one flat threshold across all 6 combos rather than 6 separately-fit numbers, given the backtest sample is still modest — avoids over-fitting) and wired `run_coverage_screener` into `screener/pipeline.py` as a genuinely separate screener alongside `run_props_screener`, reusing the same cached props API call (same default markets, so no extra API cost) rather than fetching twice
- Added a "PLAYER PROPS — COVERAGE MODEL" section to `email_report/formatter.py`, clearly described as a second independent signal, not blended with the trend model's picks
- Verified the full live chain end-to-end with real data (Breece Hall again, vs. NE): correctly predicted 18.7 receiving yards vs. a soft 25.0 line and flagged Under; correctly did NOT flag a receptions case where the edge landed just under the 20% threshold — confirms the gating logic works, not just the happy path
- Live `main.py --props-only` smoke test ran clean end-to-end (2026 pbp data doesn't exist yet — gracefully falls back to 2024-2025, same pattern as the other data fetchers already handle)
- Updated CLAUDE.md with the full story: what was tried, what failed and why, what worked, and the same "synthetic line, not a real market" caveat that already applies to the trend model

## Work Log — 2026-08-16 continuation (dashboard + full automation)
- Bradley wants to move past email-only: an interactive dashboard (click into any week/game, see all its props, filter by stat, sorted by edge, plus totals/sides), running fully automated on GitHub (not just manually-triggered), and daily now since odds shift all week — plus wants to check it himself via a link. Asked what I thought was the best way to build it before committing
- Recommended a self-contained HTML dashboard the screener generates itself (no server/backend needed, since everything is just filtering pre-computed data) over a full hosted web app — matches the actual requirements without the ongoing maintenance/cost of a real backend. Bradley agreed
- Two more asks in the same message: track the model over the year (a performance ledger — win rate/ROI over the season, not a new strategy, confirmed via question), and get odds from Bet365 specifically since that's his book
- Checked Bet365 directly against the live Odds API key across every region it supports (us, us2, uk, eu) — **not available anywhere**. Bet365 has essentially no real US market presence. Bradley chose to keep the existing cross-book average rather than pin to one US book or switch providers
- Checked daily-run API cost: player props cost one call per game, so daily automated runs (not just Mon/Wed/Fri/Sat) will likely exceed the free 500-credit/month tier once real props start posting. Bradley agreed to upgrade to the $30/month/20k-credit tier — **this needs his own payment details, I cannot do it — not confirmed done yet, check this early next session**
- Also confirmed: no GitHub repo existed yet for this project (checked `git remote -v`)
- Built the ledger (`screener/ledger.py`, permanent SQLite store, deliberately NOT gitignored — a `data/*.db` blanket-ignore rule already existed for the TTL cache, had to carve out an explicit exception) and reconciliation (`screener/reconcile.py`, reuses the exact win/push/loss grading logic already proven in the game-model backtest, adapted for live picks graded against the ledger's own recorded line/price rather than a backtest dataframe row)
- Hit and fixed a real bug during testing: reconciliation crashed calling `get_weekly_player_stats([2026])` with zero fallback years, since 2026 has no games played yet and that function raises when NO requested year has data. Added a try/except so reconciliation gracefully skips prop grading (not game grading, which only needs schedules_df) when a season's player stats don't exist yet — this is normal early-season behavior, not an error
- Verified grading logic against real 2024 historical outcomes (spread win/lose/push, total win/lose, prop over/under) and a full synthetic end-to-end test (fabricated a pick against a real completed game, ran `reconcile_all()`, got the correct result) before trusting it
- Extended `screener/pipeline.py` to capture the actual price (not just the line) for every flagged bet and resolve season/week for every matchup via schedules_df — both needed by the ledger; verified 0/156 games missing season/week on a real run
- Built the dashboard (`dashboard/build_data.py` + `template.html` + `generate.py`): groups a run's results by week → game → game_flags/props sorted by edge, plus season performance from the ledger. Rendered as a single self-contained HTML file (inline CSS/JS, no external dependencies) so it works as a local file, a GitHub Pages page, or a published artifact link without changes
- Verified visually in the Browser pane: week navigation, expand/collapse game cards, market/source filters, small-sample warning icons, column sorting, and the season-performance view (tested with synthetic picks, cleared before committing so the real ledger only has real data)
- Fixed a handful of unrounded-float display bugs surfaced while building this (e.g. a market total showing as `48.611111111111114`) in `power_ratings.py`, `player_trends.py`, `coverage_sim.py` — pre-existing, just never visually obvious in the plain-text email
- Fixed a mobile/narrow-viewport table overflow issue (added `overflow-x: auto` wrapper), since Bradley will likely check this on his phone
- **Set up the full GitHub automation:** created a public repo (`bradleyford06-ops/nfl-betting-screener`, his explicit choice for free Pages hosting), set the three repo secrets (hit and fixed a `source .env` bug along the way — bash misparsed the Gmail app password because it contains spaces; fixed by setting the secret directly with proper quoting instead of sourcing the file), rewrote the workflow to run daily (was Mon/Wed/Fri/Sat) and to commit `docs/index.html` + `data/ledger.db` back to the repo after every run (required, since GitHub Actions runners don't persist state between runs — without this the dashboard and ledger would reset every single day), enabled GitHub Pages via the API to serve from `/docs` on `main`
- **Verified the entire automated pipeline for real**, not just locally: manually triggered the Actions workflow (`gh workflow run`), watched it complete successfully (57s), confirmed via the logs that it actually sent a real email to bradleyford5@hotmail.com AND committed the updated dashboard/ledger back to the repo, then confirmed the live Pages URL (https://bradleyford06-ops.github.io/nfl-betting-screener/) actually serves the dashboard (HTTP 200, correct title) after the Pages build completed
- Added the live dashboard link to every email (top, bottom, and the "no picks" case) so Bradley can always jump from inbox to full dashboard
- Rewrote CLAUDE.md's top section, tech stack, email delivery, key directories, hard rules, and current work context to reflect that Phase 2 (automation) is now actually done, not just built — including the `docs/` folder's repurposing from "decision notes" (was always empty, no real conflict) to the GitHub Pages output location

## Pending
- **Check whether Bradley upgraded the Odds API plan** — was agreed but requires his own payment action, not confirmed done as of 2026-08-16. If still on the free tier once real props volume increases, the daily Action could start failing or silently returning incomplete odds
- Once player props start appearing (closer to the season / Wednesday-ish timing), verify BOTH props screeners against real live prop lines — still the single biggest remaining unknown in the whole pipeline, now that everything else has been verified end-to-end including full automation
- Watch QB rushing's speculative picks over the 2026 season for a real trend, now that the ledger/dashboard can actually track this properly
- Consider backtesting the remaining prop stat categories (TDs, completions, attempts) and coverage model combos (TD markets) the same way, if there's appetite
- Consider re-backtesting the game model periodically as the 2026 season accumulates real games
- Totals screener remains enabled despite no proven edge (Bradley's explicit decision)
- `data/cache.db` has grown large (160MB+) from overlapping cached year-ranges — not a current problem, worth a cleanup pass eventually
- The dashboard's mobile responsiveness was fixed for tables but not exhaustively tested on an actual phone — worth a real check once Bradley opens the link

## Blockers
None. The entire pipeline is now live and automated end-to-end: daily GitHub Actions run → screener → email with dashboard link → dashboard + ledger committed back → GitHub Pages redeploys automatically. Verified for real, not just claimed. Remaining work is validation against real season data as it accumulates (blocked on the season happening, not on us) and confirming Bradley's Odds API upgrade (blocked on him, not us).

## End of Session

Completed this session:
- Built the full NFL betting screener from scratch: two independent strategies — a power-rating model for spreads/totals/moneylines, and player props (a trend model plus, later, a second independent coverage/zone-vs-man model)
- Backtested the game model against 6 real seasons (2019-2024); found moneyline was actively unprofitable and disabled it; calibrated the spread threshold against real evidence (56.4% win rate, +9.6% ROI)
- Opponent-adjusted the props trend model, added rookie and small-sample handling, then calibrated per-position/stat thresholds using a synthetic-line backtest
- Built a coverage (zone/man) model for receiving props at Bradley's suggestion — first version failed backtesting, a simplified version passed and now runs as a second, independent signal
- Built a permanent performance ledger that auto-reconciles picks against real results
- Built an interactive dashboard (browse by week/game, filter, sort by edge, season performance)
- Set up full GitHub automation: public repo, daily scheduled runs, secrets, dashboard/ledger auto-committed, GitHub Pages hosting — verified live with real triggered runs, not just assumed
- Found and fixed a real crash in the first scheduled run (unprotected team-name lookup), verified the fix with another live run
- Confirmed Bet365 isn't available through the odds provider; confirmed daily runs need a paid Odds API tier

Still pending:
- Bradley needs to upgrade the Odds API plan (not confirmed done yet)
- Verify both prop models against real sportsbook lines once they're posted (closer to the season)
- Watch the QB-rushing speculative picks over the season for a trend
- Optional: backtest the remaining prop categories (TDs, completions, attempts), periodically re-backtest the game model as the season builds up real data

Files changed:
- `CLAUDE.md` — full project vision doc, updated repeatedly to reflect each new capability and status
- `main.py` — CLI entry point; now reconciles picks, logs to the ledger, and generates the dashboard on every full run
- `screener/pipeline.py` — orchestrates all screeners; captures price/season/week for the ledger; passes schedules_df through for lookups
- `screener/fetch_stats.py`, `screener/fetch_odds.py`, `screener/fetch_pbp.py` — data fetching (stats, odds, play-by-play), all resilient to missing years
- `screener/team_map.py` — team name/abbreviation bridge; added a static fallback after a live fetch failure crashed a scheduled run
- `screener/cache.py` — TTL cache for API responses
- `screener/ledger.py` — new: permanent SQLite picks ledger, tracked in git (not gitignored) so it survives GitHub Actions
- `screener/reconcile.py` — new: grades ledger picks against real results
- `screener/scoring.py` — ranks flagged bets
- `model/power_ratings.py` — opponent-adjusted power-rating game model; spread/total/moneyline screening
- `model/player_trends.py` — opponent-adjusted player props trend model; per-position/stat thresholds; rookie/small-sample handling
- `model/coverage_sim.py` — new: coverage (zone/man) model for receiving props, both the rejected full simulation and the live simplified version
- `backtest/` — new directory: `run_backtest.py`/`simulate.py` (game model), `run_props_backtest.py`/`simulate_props.py` (trend props), `simulate_coverage.py`/`simulate_coverage_v2.py` (coverage model)
- `dashboard/` — new directory: `build_data.py`, `template.html`, `generate.py` — generates the interactive HTML dashboard
- `docs/index.html` — new: the generated dashboard, served live via GitHub Pages
- `data/ledger.db` — new: the permanent picks ledger (real data, tracked in git)
- `email_report/formatter.py`, `email_report/send.py` — email formatting/delivery; added coverage model and speculative sections, dashboard link
- `.github/workflows/screener.yml` — GitHub Actions workflow; daily schedule, commits dashboard/ledger back after each run
- `.gitignore` — carved out an exception so `data/ledger.db` isn't caught by the `data/*.db` cache-ignore rule

Decisions made:
- Moneyline disabled (backtested actively unprofitable); totals kept on despite no proven edge (Bradley's explicit product call)
- Player props stay trend-based/simple by design; games get the heavier predictive model — Bradley's original strategic split, preserved throughout
- Coverage model runs alongside the trend model as an independent signal, not merged in or replacing it (Bradley's choice)
- QB rushing props kept live but routed to a separate "speculative" section rather than the main list
- Cross-book average odds kept instead of pinning to one book or switching providers, since Bet365 isn't available through this provider
- Public GitHub repo, to get free GitHub Pages hosting for the dashboard (vs. a paid plan for private Pages)
- Daily automated schedule instead of the original Mon/Wed/Fri/Sat plan, since odds move all week
- The ledger is committed to git rather than gitignored, since GitHub Actions runners don't persist state between runs

Blockers or warnings:
- Odds API free tier will likely fall short once real prop volume shows up — needs Bradley's upgrade (his own payment action, not something Claude can do)
- nfl_data_py's 2025 weekly player stats are currently returning 404 upstream — already handled gracefully by existing per-year fallback logic, just a live data-availability gap to be aware of
- Local git identity briefly failed to auto-detect mid-session (fixed with a repo-local config, approved by Bradley) — if it recurs, will need the same quick fix
- `data/cache.db` has grown large (160MB+) from overlapping cached year-ranges — not currently a problem, worth a cleanup pass eventually

Recommended first step next session:
Check whether Bradley has upgraded the Odds API plan yet. Then, once real player prop lines start appearing on sportsbooks (closer to the season), run `python main.py --props-only --send` (or just wait for the next daily automated run) and verify both the trend model and coverage model produce sensible, correctly-formatted picks against real posted lines for the first time — this is the single biggest remaining unknown in the whole pipeline.

Session duration: Multi-day (2026-08-14 through 2026-08-18) — one long continuous build, spanning project setup, two backtested models, a second independent props signal, a permanent performance ledger, an interactive dashboard, and full GitHub automation.
