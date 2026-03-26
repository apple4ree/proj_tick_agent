# Child Order Explosion at 500ms/delay=0 — Analysis Protocol

**Date**: 2026-03-26
**Status**: In Progress
**Purpose**: Root-cause analysis only — no engine modifications

## Objective

Determine why `500ms, market_data_delay_ms=0` produces ~7.6× more child orders
than `1s, market_data_delay_ms=0` (15,077 vs 1,979) despite nearly identical
signal counts (56 vs 54).

## Core Questions

1. What is the **direct cause** of the child order explosion?
   - Timeout cancel churn? (cancel_after_ticks × 500ms = shorter wall-clock)
   - Stale-price reprice churn? (2× evaluations/sec → more reprices)
   - Adverse selection cancel? (finer granularity → more micro-movements)
   - Parent hotspot? (few parents driving most churn)
   - Adaptation rule? (spread > 18bps → cancel_after_ticks=4 → 2s at 500ms)

2. At which **lifecycle stage** does the explosion start?
   - Signal → Parent (upstream) vs Parent → Child (downstream churn)

3. Is the explosion from **signal increase**, **parent increase**, or **per-parent
   cancel/replace churn**?

4. Does the pattern hold across **single-symbol and universe**?

## Hypotheses (ranked by prior probability)

### H1: Timeout-driven churn (primary suspect)
- `cancel_after_ticks=10` → 5s at 500ms vs 10s at 1s
- Adaptation rule: `cancel_after_ticks=4` → 2s at 500ms vs 4s at 1s
- Shorter timeout → more cancel→reslice cycles per parent lifetime
- 2× more ticks → 2× more cancel evaluation opportunities

### H2: Stale-price reprice churn (secondary)
- `max_reprices=2` (default) or `max_reprices=1` (adaptation)
- At 500ms, price checked every 500ms → more stale detections
- Each reprice creates a new child object

### H3: Timing gate interaction
- `TimingLogic(interval_seconds=1.0)` = 1 tick at 1s, 2 ticks at 500ms
- After timeout cancel, reslice fires immediately (elapsed >> 1s)
- At 500ms, shorter timeout → more frequent reslice opportunities

### H4: Adaptation rule amplification
- `spread_bps > 18` → `cancel_after_ticks=4, max_reprices=1`
- At 500ms: 4 × 0.5s = 2s timeout, 1 reprice
- Very fast cancel→reprice→timeout→reslice cycle at ~3s per round

## Run Matrix

### Required: Single-symbol (005930, 20260313)

| run_id | resample | delay_ms | strategy | purpose |
|--------|----------|----------|----------|---------|
| A | 1s | 0 | original | baseline |
| C | 500ms | 0 | original | target (explosion case) |
| B | 1s | 200 | original | control: does delay suppress explosion? |
| D | 500ms | 200 | original | control: delay=200 at 500ms |

### Optional: Universe corroboration (5 symbols, 20260313)

Same 4 combos on universe subset from revalidation benchmark:
000270, 000660, 000810, 005380, 005490

## Metrics Collected Per Run

### Basic
1. signal_count
2. parent_order_count
3. child_order_count
4. n_fills
5. cancel_rate
6. total_s / loop_s
7. canonical_tick_interval_ms
8. market_data_delay_ms

### Child lifecycle (NEW instrumentation)
9. avg_child_lifetime_seconds
10. children_per_parent (mean, median, max)
11. cancels_per_parent (mean, median, max)
12. fills_per_parent (mean)
13. replacements_per_parent (mean)

### Cancel reason decomposition
14. cancel_reason counts: timeout, replace:stale_price, adverse_selection,
    price_very_stale, max_reprices_reached, micro_event_block, other
15. cancel_reason_share (fraction of total cancels)

### Hotspot analysis
16. Top 10 parents by child_count
17. Top 10 parents by cancel_count
18. Top 10 parents by replacement_count

### Replacement chain
19. reprice_count distribution (histogram: 0, 1, 2, 3+)
20. avg reprice_count at cancel time

## Analysis Procedure

### Step 1: A vs C basic comparison
Table of basic metrics. Key judgment:
- signals/parents similar + children exploded → lifecycle churn (proceed to Step 2)
- signals/parents also exploded → upstream cadence effect

### Step 2: Cancel reason decomposition
Table: `| reason | 1s_count | 500ms_count | ratio |`
Key judgment:
- timeout dominant → H1 confirmed
- replace:stale_price dominant → H2 confirmed
- mixed → combined effect

### Step 3: Parent hotspot analysis
Per-parent child/cancel counts. Key judgment:
- concentrated (top 5 parents = 80%+ of children) → hotspot issue
- evenly distributed → systemic lifecycle pattern

### Step 4: B vs D control
Compare delay=200 results to check whether delay suppresses the explosion.

### Step 5: Universe corroboration
Per-symbol child counts to check if 005930-specific or systemic.

## Data & Strategy

| item | value |
|------|-------|
| Symbol | 005930 (Samsung Electronics) |
| Date | 20260313 |
| Strategy spec | `strategies/examples/stateful_cooldown_momentum_v2.0.json` |
| Strategy tick params | cooldown_ticks=30, holding_ticks=25, cancel_after_ticks=10 |
| Adaptation | spread>18bps → cancel_after_ticks=4, max_reprices=1 |
| Timing gate | interval_seconds=1.0 (= 1 tick at 1s, 2 ticks at 500ms) |

## Output Files

1. Protocol: `docs/analysis/child_order_explosion_500ms_d0_protocol.md` (this file)
2. Progress: `docs/analysis/child_order_explosion_500ms_d0_progress.md`
3. Results: `docs/analysis/child_order_explosion_500ms_d0_results.md`
4. Raw JSON: `outputs/benchmarks/child_order_explosion_500ms_d0.json`
5. Hotspots: `outputs/benchmarks/child_order_explosion_500ms_d0_hotspots.json`
