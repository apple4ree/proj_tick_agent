# Revalidation Benchmark Progress Log

## 2026-03-26

### Step 1: Protocol & Script (done)

- Wrote protocol: `docs/analysis/benchmark_revalidation_tick_time_alignment_protocol.md`
- Created benchmark script: `scripts/internal/adhoc/benchmark_revalidation_tick_time_alignment.py`
  - Adapted from `benchmark_observation_lag_2x2.py`
  - Added `canonical_tick_interval_ms` to RunResult
  - Added pre-alignment comparison output
  - All 3 phases in one script: raw 2x2, normalized control, universe

### Step 2: Run benchmark (done)

**Phase 1: Single-symbol raw 2x2** — completed
- A (1s/d=0): 17.8s, 54 signals, 1979 children, 56 fills
- B (1s/d=200): 17.4s, 94 signals, 773 children, 101 fills
- C (500ms/d=0): 895.8s (!), 56 signals, 15077 children, 60 fills
- D (500ms/d=200): 47.1s, 98 signals, 990 children, 115 fills

**Phase 2: Normalized control** — completed
- C_n (500ms/d=0, 2×): 18.0s, 34 signals, 339 children, 38 fills
- D_n (500ms/d=200, 2×): 41.3s, 92 signals, 1234 children, 101 fills

**Phase 3: Universe raw 2x2** — completed
- A: 5/5 timeout (same as pre-alignment: 4/5 timeout)
- B: 5/5 ok, 375 signals, 516 fills (pre: 319 signals, 407 fills)
- C: 5/5 timeout (same as pre-alignment: 5/5 timeout)
- D: 5/5 ok, 249 signals, 401 fills (pre: 219 signals, 282 fills)

### Key observations (vs pre-alignment)

1. **Run A (1s/d=0) dramatically faster**: 17.8s vs 734s (41× faster)
   - children: 1,979 vs 17,910 (9× fewer)
   - signals: 54 vs 6 (9× more — orders now survive long enough to fill and trigger new entries)
   - PnL flipped: -1.35M vs +83K

2. **Run C (500ms/d=0) still pathologically slow**: 895.8s (was 932s)
   - children: 15,077 (still high due to 5s cancel timeout at 500ms)
   - This is a genuine cadence effect, not a bug — shorter tick = shorter cancel timeout = more resubmissions

3. **Run C_n (normalized 500ms/d=0) is fast**: 18.0s — 2× cancel_after_ticks=20 gives 10s timeout, matching 1s

4. **Normalized decomposition qualitatively changed**:
   - Pre-alignment: cadence norm was exactly zero (A ≡ C_n)
   - Post-alignment: cadence norm is -20 signals (C_n < A) — 500ms normalized generates FEWER signals

5. **Universe delay=0 still times out**: Fix didn't resolve the per-symbol timeout issue for delay=0

### Step 3: Write results report (done)

- Results: `docs/analysis/benchmark_revalidation_tick_time_alignment_results.md`
- Raw JSON: `outputs/benchmarks/benchmark_revalidation_tick_time_alignment.json`
- All output files generated as specified in protocol
