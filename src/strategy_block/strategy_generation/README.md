# strategy_generation/ — v2 전략 생성

연구 목표(goal)를 입력받아 StrategySpecV2를 생성한다. **두 가지 backend**를 지원한다:

| Backend | 경로 | 설명 |
|---------|------|------|
| `template` (기본) | goal keyword → template 선택 → lower_to_spec_v2 → review | 결정론적, API 불필요 |
| `openai` | goal → OpenAI structured plan → lower_plan_to_spec_v2 → review | LLM 기반, API 키 필요 (mock/replay 모드 제공) |

## 핵심 역할

- Goal 키워드 매칭으로 적합한 템플릿 선택 (template backend)
- OpenAI structured output으로 중간 plan 생성 후 lowering (openai backend)
- 생성된 spec에 대해 자동 정적 리뷰 (hard gate)
- Trace metadata 기록 (provenance, fallback 여부)
- OpenAI 실패 시 template fallback 정책 (`allow_template_fallback`)

## 대표 파일

| 파일 | 역할 |
|------|------|
| `generator.py` | `StrategyGenerator` — 생성 진입점. backend 분기, 리뷰 gate, fallback 정책 |
| `openai_client.py` | OpenAI API 래퍼 (live/replay/mock 모드). `query_structured()` + `mock_factory` |
| `v2/templates_v2.py` | 12개 v2 템플릿 정의 (Phase 1~3). 중간 dict 형태 |
| `v2/lowering.py` | 템플릿 dict/plan → `StrategySpecV2` 변환. `lower_to_spec_v2()` + `lower_plan_to_spec_v2()` |
| `v2/openai_generation.py` | OpenAI v2 생성 모듈. plan 생성 → lowering → review |
| `v2/schemas/plan_schema.py` | 중간 plan 스키마 (`StrategyPlan`). OpenAI가 이 형식을 출력 |
| `v2/utils/prompt_builder.py` | 시스템/유저 프롬프트 빌더 |
| `v2/utils/response_parser.py` | OpenAI 응답 파싱 + plan 검증 |
| `v2/prompts/` | 고정 프롬프트 템플릿 (planner_system.md, planner_user.md) |

## 생성 흐름

### Template Backend (default)

```
--goal "order imbalance alpha" --backend template
  → StrategyGenerator.generate()
  → _V2_GOAL_KEYWORDS로 키워드 매칭 → 템플릿 선택
  → lower_to_spec_v2() → StrategySpecV2
  → StrategyReviewerV2.review() (hard gate)
  → (spec, trace) 반환
```

### OpenAI Backend

```
--goal "mean reversion on imbalance" --backend openai --mode mock
  → StrategyGenerator.generate()
  → _generate_openai_v2()
  → generate_spec_v2_with_openai()
    → generate_plan_with_openai()
      → build_system_prompt() + build_user_prompt()
      → OpenAIStrategyGenClient.query_structured(schema=StrategyPlan)
      → parse_plan_response() → StrategyPlan
    → lower_plan_to_spec_v2() → StrategySpecV2
    → StrategyReviewerV2.review() (hard gate)
  → (spec, trace) 반환

PlanParseError 시:
  allow_template_fallback=true → template 경로로 fallback
  allow_template_fallback=false → StaticReviewError raise
```

### 핵심 설계 원칙

- OpenAI는 **중간 plan (StrategyPlan)** 을 생성한다 — 최종 AST를 직접 생성하지 않음
- Plan → SpecV2 lowering은 **결정론적 코드** 가 수행한다
- Template 경로는 변경 없이 그대로 유지된다
- Plan schema는 **OpenAI structured outputs strict schema** 에 맞게 설계됨:
  - `dict[str, X]` 동적 맵 없음 — `list[StateVarPlan]` 등 list-of-objects 사용
  - `minimum`/`maximum` 등 OpenAI가 지원하지 않는 JSON schema 키워드 없음
  - 재귀 구조(`ConditionPlan.children`)는 `$ref`로 처리 (OpenAI 지원)

## OpenAI 모드

| 모드 | 설명 | API 키 필요 |
|------|------|------------|
| `live` | 실제 OpenAI API 호출 | O |
| `mock` | 결정론적 fixture plan 생성 (goal 키워드별 분기) | X |
| `replay` | 저장된 응답 로그 재생 | X |

## 내장 템플릿 (v2/templates_v2.py)

| Phase | 템플릿 | 핵심 전략 |
|-------|--------|----------|
| 1 | imbalance_persist_momentum | 지속적 order imbalance 모멘텀 |
| 1 | spread_absorption_reversal | 스프레드 확대 시 imbalance 반전 |
| 1 | cross_momentum | trade flow cross 모멘텀 |
| 2 | regime_filtered_persist_momentum | regime 라우팅 + persist |
| 2 | rolling_mean_reversion | rolling window 평균 회귀 |
| 2 | adaptive_execution_imbalance | execution adaptation |
| 3 | stateful_cooldown_momentum | cooldown 상태 추적 |
| 3 | loss_streak_degraded_reversion | 손실 연속 시 degradation |
| 3 | latency_adaptive_passive_entry | latency 기반 passive 진입 |

## Fallback 정책

| 설정 | 값 | 동작 |
|------|---|------|
| `allow_template_fallback` | true | OpenAI PlanParseError 시 template fallback |
| `allow_template_fallback` | false | OpenAI 실패 시 StaticReviewError raise |
| `fail_on_fallback` | true | fallback 발생 자체를 에러로 처리 |
| `fail_on_fallback` | false | fallback 성공이면 결과 반환 |

## 주의사항

- 새 템플릿 추가: `templates_v2.py`에 dict 추가 + `_V2_GOAL_KEYWORDS`에 키워드 매핑
- Lowering은 compact form(`{feature, op, threshold}`)과 explicit form(`{type, ...}`) 양쪽 지원
- Plan lowering은 `ConditionPlan`의 7가지 형태(feature/state_var/position_attr/composite/cross/persist/rolling) 모두 지원
- `StaticReviewError` 발생 시 생성 실패 (hard gate)
- Trace provenance에 `requested_backend`, `effective_backend`, `generation_class` 기록
- **plan schema 수정 시**: `StrategyPlan.model_json_schema()`가 OpenAI strict schema와 호환되는지 반드시 확인
  - `additionalProperties` 금지 (동적 map 사용 불가)
  - `minimum`/`maximum` 금지 (`ge`/`le` Field 제약 사용 불가)
  - 테스트: `TestSchemaCompatibility` 참조

## 관련 문서

- [../strategy_specs/README.md](../strategy_specs/README.md) — StrategySpecV2 스키마
- [../strategy_review/README.md](../strategy_review/README.md) — 정적 리뷰 규칙
- [../../../../conf/README.md](../../../../conf/README.md) — generation.yaml 설정
