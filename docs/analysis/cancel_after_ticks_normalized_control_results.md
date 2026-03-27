# Normalized `cancel_after_ticks` Control Experiment — Results

**Date**: 2026-03-26
**Status**: Final

## Conclusion

**`cancel_after_ticks normalization has little effect; the root cause lies elsewhere.`**

Doubling `cancel_after_ticks` (10→20, adaptation 4→8) at 500ms/delay=0 reduced
total children by only 34 out of 15,077 (−0.2%). The dominant hotspot parent
remained at ~13,174 children with **zero fills**. Average child lifetime was
unchanged (0.523s → 0.526s). The child-order explosion is driven almost entirely
by **adverse_selection cancellation**, which fires within ~0.5s — far before the
cancel timeout would trigger (5s baseline, 10s control).

---

## Table 1: Baseline vs Control Basic Comparison (005930)

| metric | A (1s/d=0) | C (500ms/d=0) | C_ctrl (500ms/d=0 cancel×2) | C/A | C_ctrl/C |
|--------|-----------|---------------|----------------------------|-----|----------|
| signals | 54 | 56 | 56 | 1.04× | 1.00× |
| parents | 54 | 56 | 56 | 1.04× | 1.00× |
| **children** | **1,979** | **15,077** | **15,043** | **7.6×** | **1.00×** |
| fills | 56 | 60 | 61 | 1.07× | 1.02× |
| cancel_rate | 97.27% | 99.64% | 99.63% | — | — |
| **children/parent** | **36.6** | **269.2** | **268.6** | **7.4×** | **1.00×** |
| **avg_child_lifetime_s** | **1.307** | **0.523** | **0.526** | **0.40×** | **1.01×** |
| loop_s | 7.8 | 870.3 | 879.7 | 112× | 1.01× |
| net_pnl | −1,354,450 | −1,400,723 | −1,389,711 | — | — |

### Key observation

C vs C_ctrl is a near-perfect identity:
- children: −34 (−0.2%)
- children/parent: −0.6 (−0.2%)
- avg_child_lifetime: +0.003s (+0.6%)
- loop_s: +9.4s (+1.1%, noise)
- fills: +1 (negligible)

**Doubling cancel_after_ticks had essentially zero effect on the child explosion.**

---

## Table 2: Cancel Reason Decomposition

| reason | A count | A % | C count | C % | C_ctrl count | C_ctrl % |
|--------|---------|-----|---------|-----|-------------|----------|
| **adverse_selection** | **1,864** | **96.8%** | **14,955** | **99.6%** | **14,956** | **99.8%** |
| timeout | 61 | 3.2% | 65 | 0.4% | 30 | 0.2% |
| micro_event_block | 0 | 0.0% | 1 | 0.0% | 1 | 0.0% |
| unknown | 0 | 0.0% | 1 | 0.0% | 1 | 0.0% |
| **TOTAL** | **1,925** | **100%** | **15,022** | **100%** | **14,988** | **100%** |

### Interpretation

- Timeout cancels dropped: 65 → 30 (−54%). This is the **only** visible effect
  of doubling cancel_after_ticks. But timeout is only 0.4% of cancels at baseline.
- Adverse_selection count stayed virtually identical: 14,955 → 14,956.
- The 35 fewer timeout cancels → 34 fewer total children. That's the entire
  reduction: the timeout cancels that no longer fire, replaced by adverse_selection
  cancels (which dominate before the new timeout would trigger).

### delay=200 comparison

| reason | B (1s/d=200) | B % | D (500ms/d=200) | D % | D_ctrl (500ms/d=200 cancel×2) | D_ctrl % |
|--------|-------------|-----|-----------------|-----|-------------------------------|----------|
| adverse_selection | 578 | 85.1% | 746 | 83.6% | 781 | 92.2% |
| timeout | 97 | 14.3% | 142 | 15.9% | 62 | 7.3% |

At delay=200, doubling cancel_after_ticks had a more visible effect:
- D → D_ctrl: children 990 → 945 (−4.5%), timeout 142 → 62 (−56%)
- But adverse_selection % actually **increased** (83.6% → 92.2%) as timeout
  cancels were absorbed into adverse_selection

---

## Table 3: Dominant Hotspot Parent Comparison

| run | side | children | % total | cancels | fills | avg_lifetime_s | dominant_reason |
|-----|------|----------|---------|---------|-------|----------------|-----------------|
| A | SELL | 1,033 | 52.2% | 1,032 | **1** | 1.044 | adverse_selection |
| **C** | **SELL** | **13,177** | **87.4%** | **13,177** | **0** | **0.502** | **adverse_selection** |
| **C_ctrl** | **SELL** | **13,174** | **87.6%** | **13,174** | **0** | **0.502** | **adverse_selection** |
| D | SELL | 289 | 29.2% | 288 | 1 | 1.109 | adverse_selection |
| D_ctrl | SELL | 285 | 30.2% | 284 | 1 | 1.118 | adverse_selection |

### Critical finding

The dominant parent at 500ms/d=0 is **completely unaffected** by cancel timer normalization:
- C: 13,177 children, 0 fills, avg_life=0.502s
- C_ctrl: 13,174 children, 0 fills, avg_life=0.502s

The dominant parent's children are cancelled by adverse_selection at ~0.502s.
The cancel timeout (5s baseline, 10s control) never fires because adverse_selection
triggers at least 10× sooner.

At 1s/d=0 (run A), the equivalent parent gets **1 fill** in 1,033 attempts.
That single fill completes the parent and breaks the churn cycle. At 500ms,
the queue model cannot advance in 0.5s — no amount of cancel_after_ticks
lengthening changes this.

---

## Table 4: C vs C_ctrl Detailed Delta

| metric | C | C_ctrl | delta | ratio |
|--------|---|--------|-------|-------|
| signals | 56 | 56 | 0 | 1.00× |
| parents | 56 | 56 | 0 | 1.00× |
| children | 15,077 | 15,043 | −34 | 1.00× |
| children/parent | 269.2 | 268.6 | −0.6 | 1.00× |
| fills | 60 | 61 | +1 | 1.02× |
| cancel_rate | 99.64% | 99.63% | −0.01pp | — |
| avg_child_lifetime_s | 0.523 | 0.526 | +0.003 | 1.01× |
| loop_s | 870.3 | 879.7 | +9.4 | 1.01× |
| net_pnl | −1,400,723 | −1,389,711 | +11,012 | — |

Every metric is within noise. The experiment conclusively shows that
`cancel_after_ticks` is not a meaningful lever for this phenomenon.

---

## Answers to Core Questions

### 1. Does cancel_after_ticks ×2 meaningfully reduce child count?

**No.** Children dropped from 15,077 to 15,043 (−0.2%). This is entirely
explained by 35 fewer timeout cancels.

### 2. Is the reduction from lifecycle churn decrease?

**Yes, but it's negligible.** The 34 fewer children come from timeout cancels
that no longer fire. Signal/parent counts are identical (56/56). The churn
mechanism (adverse_selection) is untouched.

### 3. Does the dominant hotspot parent escape zero-fill churn?

**No.** The dominant parent still has 13,174 children and zero fills.
Average lifetime is unchanged at 0.502s. It churns for the entire session.

### 4. Does this support "wall-clock cancel cadence is a key root-cause axis"?

**No.** The experiment refutes the hypothesis that cancel_after_ticks
wall-clock shortening drives the explosion. The root cause is
**adverse_selection detection at finer temporal granularity**, not
timeout-driven cancel→reslice churn. Timeout accounts for only 0.4% of
cancels, and even halving that to 0.2% has negligible impact.

---

## Why cancel_after_ticks Doesn't Matter

The prior root-cause analysis found that 99.6% of cancels are `adverse_selection`.
This experiment confirms the implication: the cancel timer is irrelevant because
children are killed by adverse_selection within ~0.5s — well before the 5s
(baseline) or 10s (control) timeout fires.

```
Child lifecycle at 500ms/d=0:
  submitted → 0.5s → adverse_selection cancel → 1.0s timing gate → re-slice
                     ↑ this happens at 0.5s
                       cancel_after_ticks timeout = 5s (baseline) or 10s (control)
                       → NEVER REACHED
```

The only children affected by cancel_after_ticks are the ~0.4% that survive
long enough without adverse mid-moves. Doubling the timer for those few
children saves ~35 cancels — noise against 15,000.

---

## Revised Root Cause Understanding

The child-order explosion at 500ms/d=0 is driven by a single mechanism:

1. **Adverse_selection at 500ms granularity**: mid-price moves > 10 bps are
   detected within 1 tick (0.5s). At 1s, the same move takes ~1.3 ticks.
2. **Queue model starvation**: passive limit orders are cancelled at 0.5s
   average — insufficient time for queue advancement to produce a fill.
3. **Dominant parent lock-in**: a single SELL parent never fills, churning
   ~13,174 children over the entire session.

`cancel_after_ticks` is orthogonal to this mechanism. The timer never fires
because adverse_selection fires first.

---

## Recommended Next Steps

1. **Adverse selection threshold sensitivity**: Test `adverse_selection_threshold_bps`
   at {10, 15, 20, 30} to measure churn reduction. This directly targets the
   actual cancel mechanism (99.6% of cancels).

2. **Queue advancement audit**: Measure how close the dominant parent's children
   get to fill before adverse_selection cancels them. If they accumulate 80%+
   queue credit, a modest threshold increase may break the cycle.

3. **Minimum child lifetime floor**: Test a minimum hold time (e.g., 2s) before
   adverse_selection can trigger. This would give queue model time to advance.

4. **Position-side drift analysis**: The dominant parent is always SELL-side.
   Investigate whether there's a systematic upward price drift that makes
   adverse_selection structurally inevitable for sell orders.

**Note**: These are diagnostic experiments. The current engine behavior is
mechanistically correct — it faithfully simulates what happens when passive
limit orders face adverse selection at 500ms granularity with 10 bps threshold.
