# Child Order Explosion Analysis — Progress Log

## 2026-03-26

### Step 1: Engine research (done)

Key findings:
- `TimingLogic(interval_seconds=1.0)` → new child every 1s wall-clock
- Cancel/replace evaluates every tick → 2× more evaluations at 500ms
- `cancel_after_ticks=10` → 5s at 500ms, 10s at 1s
- Adaptation rule: spread>18bps → `cancel_after_ticks=4, max_reprices=1` → 2s at 500ms
- Replacement updates `last_child_submission`, affecting timing gate
- Loop order: (1) cancel/replace, (2) slice — in same tick
- `effective_remaining` check prevents new slice when in-flight child exists
- `child.meta["cancel_reason"]` captures reason; `child.meta["reprice_count"]` tracks reprices

### Step 2: Protocol written (done)

- `docs/analysis/child_order_explosion_500ms_d0_protocol.md`

### Step 3: Analysis script built (done)

- `scripts/internal/adhoc/analyze_child_order_explosion_500ms_d0.py`
- Monkey-patches `ReportBuilder.generate_reports` to capture parent_orders
- Computes: cancel reason counts, reprice histograms, per-parent hotspots, lifecycle ratios
- Runs A/B/C/D single-symbol + universe A/C corroboration

### Step 4: Run analysis

- Status: **starting**
