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
- static review 앞단 leakage/liveness lint(feature-time/lookahead/fill-alignment/latency-feasibility) 통과
- generation trace metadata 기록 (provenance, fallback, execution-policy explicitness)
- OpenAI generation static review 실패 시 fixable execution/horizon 이슈에 대해 deterministic rescue 1회 + 재검토

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

운영 관점:
- generation 목표는 단순히 "좋아 보이는" 아이디어 생성이 아니라,
  deterministic static/leakage gate를 통과 가능한 후보를 만드는 데 가깝다.

## Deterministic Generation Rescue (OpenAI v2)

generation은 reviewer hard gate를 완화하지 않고, static review fail 이후에만 제한적 deterministic rescue를 수행한다.

원칙:
- rescue는 LLM 추가 호출 없이 deterministic patch만 허용
- rescue 시도는 정확히 1회만 수행
- 보정 범위는 `execution_policy`/`holding_ticks`/passive repricing envelope/fail-safe time exit 보강으로 제한
- entry trigger/signal logic/alpha hypothesis는 변경하지 않음

rescue 대상(fixable execution/horizon):
- `FEATURE_TIME_NEAR_ZERO_HORIZON`
- short-horizon + missing `execution_policy`
- short-horizon + overly aggressive passive repricing
- `latency_feasibility_risk` 중 execution-policy/horizon 완화로 해결 가능한 이슈
- `churn_risk_high` 중 repricing/cancel horizon 완화로 해결 가능한 이슈

rescue 비대상(즉시 실패 유지):
- schema/parsing 오류
- lookahead/leakage 본질 오류
- fill-alignment 오류
- invalid AST/unsupported feature/invalid response structure

trace:
- `pre_review_flags`
- `generation_rescue_attempted`, `generation_rescue_applied`, `generation_rescue_operations`
- `rescue`
- `post_rescue_review`

목적은 fixable OpenAI output의 실패율을 낮추는 것이며, reviewer semantics를 느슨하게 만드는 것이 아니다.

## Related Files

- `generator.py`
- `v2/openai_generation.py`
- `v2/lowering.py`
- `v2/schemas/plan_schema.py`
- `v2/utils/prompt_builder.py`
- `v2/prompts/`
- `../strategy_registry/trial_registry.py` (trial candidate record foundation)
- `../strategy_registry/lineage.py` (parent-child lineage foundation)
- `../strategy_registry/family_fingerprint.py` (coarse family signature foundation)
- `../strategy_registry/family_index.py` (family member/near-duplicate index foundation)

## Known Limitations / Deferred Scope

- generation은 backtest 엔진 semantics를 소유하지 않음
- live/replay LLM 경로는 runtime/provider 상태에 따라 변동 가능
- deterministic baseline은 mock mode 기준
- family dedupe/fingerprint는 registry foundation으로 제공되며 generation 단계에 novelty 제약을 직접 강제하지는 않음

## Freeze Reference

Phase 4 baseline snapshot:
- `outputs/benchmarks/phase4_benchmark_freeze.json`
- `outputs/benchmarks/phase4_benchmark_freeze.md`
