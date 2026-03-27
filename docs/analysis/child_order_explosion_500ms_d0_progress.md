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

### Step 4: Run analysis (done)

**Phase 1: Single-symbol (005930)**
- A (1s/d=0): 17.6s, 54 signals, 1,979 children, 36.6 ch/parent, avg_life=1.307s
- B (1s/d=200): 21.3s, 94 signals, 773 children, 8.2 ch/parent, avg_life=3.266s
- C (500ms/d=0): 888.8s, 56 signals, 15,077 children, 269.2 ch/parent, avg_life=0.523s
- D (500ms/d=200): 47.5s, 98 signals, 990 children, 10.1 ch/parent, avg_life=1.708s

Cancel reason:
- A: 96.8% adverse_selection, 3.2% timeout, 0% replacement
- C: 99.6% adverse_selection, 0.4% timeout, 0% replacement

Hotspot:
- A: top parent = 1,033 children (52.2%), 1 fill
- C: top parent = 13,177 children (87.4%), 0 fills

**Phase 2: Universe corroboration**
- All 5 symbols timed out at both 1s/d=0 and 500ms/d=0
- Confirms systemic pattern, not 005930-specific

### Step 5: Results written (done)

- Results: `docs/analysis/child_order_explosion_500ms_d0_results.md`
- Raw JSON: `outputs/benchmarks/child_order_explosion_500ms_d0.json`
- Hotspots: `outputs/benchmarks/child_order_explosion_500ms_d0_hotspots.json`
