# Performance Analysis

Reusable code for analyzing how the live picks in `data/ledger.db` are actually performing —
separate from `backtest/` (which simulates past seasons) and `screener/` (which makes today's
picks). This is for looking backward at real results and finding trends, once there's enough
graded data to say something meaningful.

Started 2026-09-15 after the first full week of graded player props and a few weeks of CFB
spread picks. See `.claude/memory/sessions/2026-09-15-stats-fallback-and-first-analysis.md`
for what that first review found.

## What's here

- **`props_performance.py`** — load graded player-prop picks, attach each pick's position,
  break down win rate by any combination of strategy/market/position/side/edge size, and
  test whether a gap between two groups is statistically real or likely noise.
- **`cfb_performance.py`** — same idea for CFB spread/total picks, plus two CFB-specific
  tools: recovering the model's predicted margin from a pick's explanation text (for picks
  made before `predicted_value` was tracked directly), and checking a live stretch against
  the backtest's own week-to-week variance to tell a real problem from normal noise.

## Example

```python
from analysis.props_performance import load_graded_props, attach_position, win_rate_table

df = load_graded_props(season=2026, week=1)
df = attach_position(df, stats_years=[2025, 2026])
print(win_rate_table(df, ["position"]))
```

## Picks made from 2026-09-15 onward

Every pick now carries `predicted_value` and `predicted_value_type` directly in the ledger
(see `screener/ledger.py`) — the model's own raw predicted number, not just win/loss. That
makes "how far off was the model, on average" a direct query instead of the text-parsing
`cfb_performance.attach_predicted_margins` had to do for older picks. Prefer it once there's
enough post-2026-09-15 data to analyze.
