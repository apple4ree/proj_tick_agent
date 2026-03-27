# Adverse Selection Threshold Sensitivity — Progress Log

## 2026-03-27

### Step 1: Protocol and setup validation (done)

- Confirmed baseline context from:
  - `docs/analysis/child_order_explosion_500ms_d0_results.md`
  - `docs/backtest_realism_design.md`
- Fixed experiment setup:
  - symbol `005930`
  - date `20260313`
  - strategy `strategies/examples/stateful_cooldown_momentum_v2.0.json`
  - `resample=500ms`
  - `market_data_delay_ms=0`
  - thresholds `{10, 15, 20, 30}`

### Step 2: Experiment runner added (done)

- Added script:
  - `scripts/internal/adhoc/run_adverse_selection_threshold_sensitivity.py`
- Approach:
  - No engine/CLI changes
  - Script-local `PipelineRunner` subclass injects only
    `CancelReplaceLogic.adverse_selection_threshold_bps`
  - Reuses existing pipeline and report flow
  - Captures parent-order aggregate via `ReportBuilder.generate_reports` monkey-patch
- Output target:
  - `outputs/benchmarks/adverse_selection_threshold_sensitivity.json`

### Step 3: Run experiment matrix (done)

Execution command:

```bash
cd /home/dgu/tick/proj_rl_agent
PYTHONPATH=src python scripts/internal/adhoc/run_adverse_selection_threshold_sensitivity.py
```

Execution highlights:

- Warmup: done
- State build (`500ms`): 41,720 states (52.47s)
- Threshold runs:
  - `10bps`: total 40.15s, children 1,366, fills 128
  - `15bps`: total 18.20s, children 227, fills 52
  - `20bps`: total 19.09s, children 175, fills 54
  - `30bps`: total 18.88s, children 162, fills 54

UTC completion timestamp: `2026-03-27 07:14:30 UTC`

### Step 4: Artifacts generated (done)

- Raw JSON:
  - `outputs/benchmarks/adverse_selection_threshold_sensitivity.json`
- Documentation:
  - Protocol: `docs/analysis/adverse_selection_threshold_sensitivity_protocol.md`
  - Progress: `docs/analysis/adverse_selection_threshold_sensitivity_progress.md` (this file)
  - Results: `docs/analysis/adverse_selection_threshold_sensitivity_results.md`
