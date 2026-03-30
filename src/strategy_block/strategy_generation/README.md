# strategy_generation/ — v2 전략 생성

목표(goal) 기반으로 `StrategySpecV2`를 생성한다.

## Document Scope

이 문서는 generation 계층의 **현재 canonical 동작(Tier 1)** 을 설명한다.
Baseline/회귀 기준은 Tier 2 freeze 문서를 따른다.

- `docs/analysis/benchmark_freeze_protocol.md`
- `docs/analysis/benchmark_freeze_results.md`
- `docs/analysis/benchmark_freeze_baselines.md`

## 핵심 책임

- template/openai backend로 spec 생성
- OpenAI 경로에서 structured plan 생성 후 deterministic lowering
- 생성 직후 static review hard gate 실행
- generation trace metadata 기록 (provenance, fallback, execution-policy explicitness)

## 책임 경계 (중요)

- generation은 backtest environment-aware prompt를 사용한다.
- 하지만 queue/fill/latency semantics의 owner는 backtest runtime이다.
- generation은 semantics를 재정의하지 않고, 현재 환경 제약을 반영한 spec을 산출한다.

## Backend

| Backend | 설명 |
|---|---|
| `template` | 결정론적 템플릿 기반 생성 |
| `openai` | structured `StrategyPlan` 생성 후 lowering |

OpenAI 실패 시 `allow_template_fallback` 정책에 따라 template fallback 가능.

## Public CLI Surface

`scripts/generate_strategy.py` public 옵션:
- `--goal`
- `--backend`
- `--config`
- `--profile`
- `--direct`

`mode/model/auto_approve`는 CLI가 아니라 config에서 읽는다.

## Generation Flow

### template

```
goal -> template select -> lower_to_spec_v2 -> static review
```

### openai

```
goal -> prompt build -> StrategyPlan(structured output)
     -> lower_plan_to_spec_v2 -> static review
```

원칙:
- OpenAI는 최종 spec를 자유 재작성하지 않는다.
- lowering/검증은 deterministic 코드가 담당한다.

## Canonical Backtest Constraint Summary Injection

OpenAI generation prompt에는 canonical summary block이 주입된다.

포함 축:
- cadence (`resample`, `canonical_tick_interval_ms`, `tick = resample step`)
- observation/decision delay (`market_data_delay_ms`, `decision_compute_ms`, `effective_delay_ms`)
- venue latency (`order_submit_ms`, `order_ack_ms`, `cancel_ms`, `order_ack_used_for_fill_gating`)
- queue semantics (`queue_model`, `queue_position_assumption`)
- replace semantics (minimal immediate)
- friction implication (queue waiting, repricing reset, churn cost)

generation/review prompt는 같은 canonical wording contract를 공유한다.

## Execution Policy Explicitness Policy

현행 정책:
- short-horizon 전략은 `execution_policy` 생략 금지 수준으로 유도
- 특히 아래 필드를 명시적으로 출력하도록 요구
  - `placement_mode`
  - `cancel_after_ticks`
  - `max_reprices`
- low-churn execution policy를 선호

trace metadata 예:
- `execution_policy_explicit`
- `execution_policy_missing_short_horizon`
- `inferred_holding_horizon_ticks`

## Related Files

- `generator.py`
- `v2/openai_generation.py`
- `v2/lowering.py`
- `v2/schemas/plan_schema.py`
- `v2/utils/prompt_builder.py`
- `v2/prompts/`

## Known Limitations / Deferred Scope

- generation은 backtest 엔진 semantics를 소유하지 않음
- live/replay LLM 경로는 runtime/provider 상태에 따라 변동 가능
- deterministic baseline은 mock mode 기준

## Freeze Reference

Phase 4 baseline snapshot:
- `outputs/benchmarks/phase4_benchmark_freeze.json`
- `outputs/benchmarks/phase4_benchmark_freeze.md`
