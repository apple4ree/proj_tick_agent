# Normalized cancel_after_ticks Control Experiment — Progress Log

## 2026-03-26

### Step 1: Protocol written (done)

- `docs/analysis/cancel_after_ticks_normalized_control_protocol.md`

### Step 2: Analysis script built (done)

- `scripts/internal/adhoc/run_cancel_after_ticks_normalized_control.py`
- Monkey-patches `ReportBuilder.generate_reports` to capture parent_orders
- `_make_cancel_only_normalized_spec()` scales ONLY cancel_after_ticks (10→20, 4→8)
- cooldown_ticks, holding_ticks, max_reprices unchanged
- Runs A/C/C_ctrl + optional B/D/D_ctrl
- Collects: cancel reasons, reprice histograms, per-parent hotspots, lifecycle ratios

### Step 3: Run all 6 experiments (done)

**All runs completed successfully (005930, 20260313)**

| run_id | resample | delay | cancel_after_ticks | elapsed | children | ch/parent | avg_life |
|--------|----------|-------|--------------------|---------|----------|-----------|----------|
| A | 1s | 0 | baseline (10/4) | 17.7s | 1,979 | 36.6 | 1.307s |
| C | 500ms | 0 | baseline (10/4) | 891.6s | 15,077 | 269.2 | 0.523s |
| C_ctrl | 500ms | 0 | 2× (20/8) | 901.5s | 15,043 | 268.6 | 0.526s |
| B | 1s | 200 | baseline (10/4) | 22.1s | 773 | 8.2 | 3.266s |
| D | 500ms | 200 | baseline (10/4) | 43.5s | 990 | 10.1 | 1.708s |
| D_ctrl | 500ms | 200 | 2× (20/8) | 45.5s | 945 | 9.6 | 1.800s |

**Baseline reproduction confirmed:**
- A: 54 signals, 1,979 children, 36.6 ch/parent (matches prior: 54/1,979/36.6)
- C: 56 signals, 15,077 children, 269.2 ch/parent (matches prior: 56/15,077/269.2)

**Key finding:**
- C → C_ctrl: children 15,077 → 15,043 (−34, −0.2%). **Essentially no effect.**
- Dominant parent: 13,177 → 13,174 children, zero fills in both.
- avg_child_lifetime: 0.523s → 0.526s (unchanged).
- Timeout cancels dropped 65 → 30, but adverse_selection stayed at 99.6%→99.8%.

### Step 4: Results written (done)

- Results: `docs/analysis/cancel_after_ticks_normalized_control_results.md`
- Raw JSON: `outputs/benchmarks/cancel_after_ticks_normalized_control.json`
- Hotspots: `outputs/benchmarks/cancel_after_ticks_normalized_control_hotspots.json`
