# strategy_generation/ — v2 전략 생성

연구 목표(goal)를 입력받아 StrategySpecV2를 생성한다. 현재 canonical path는 **v2 template 기반 생성**이다.

## 핵심 역할

- Goal 키워드 매칭으로 적합한 템플릿 선택
- 템플릿(중간 dict) → StrategySpecV2 lowering
- 생성된 spec에 대해 자동 정적 리뷰 (hard gate)
- Trace metadata 기록 (provenance, fallback 여부)

## 대표 파일

| 파일 | 역할 |
|------|------|
| `generator.py` | `StrategyGenerator` — 생성 진입점. 백엔드 선택, 템플릿 매칭, 리뷰 gate |
| `v2/templates_v2.py` | 9+개 v2 템플릿 정의 (Phase 1~3). 중간 dict 형태 |
| `v2/lowering.py` | 템플릿 dict → `StrategySpecV2` 변환. 각 policy별 lowering 함수 |
| `openai_client.py` | OpenAI API 래퍼 (live/replay/mock 모드). 현재 v2에서 실질적 미사용 |

## 생성 흐름

```
--goal "order imbalance alpha"
  → StrategyGenerator.generate()
  → _V2_GOAL_KEYWORDS로 키워드 매칭 → 템플릿 선택
  → lower_template_to_spec() → StrategySpecV2
  → StrategyReviewerV2.review() (hard gate)
  → (spec, trace) 반환
```

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

## OpenAI Backend 현재 상태

- `openai_client.py`에 구조는 준비되어 있으나, `generator.py`에서 **openai backend 요청 시 template으로 자동 fallback**
- Fallback 시 warning + trace에 기록
- `--mode mock`으로 API 키 없이 fallback 로직 테스트 가능
- 실질적으로 v2 생성은 template-only

## 주의사항

- 새 템플릿 추가 시 `templates_v2.py`에 dict 추가 + `_V2_GOAL_KEYWORDS`에 키워드 매핑
- Lowering은 compact form(`{feature, op, threshold}`)과 explicit form(`{type, ...}`) 양쪽 지원
- `StaticReviewError` 발생 시 생성 실패 (hard gate)
- `fail_on_fallback` 정책으로 fallback 자체를 에러로 취급 가능

## 관련 문서

- [../strategy_specs/README.md](../strategy_specs/README.md) — StrategySpecV2 스키마
- [../strategy_review/README.md](../strategy_review/README.md) — 정적 리뷰 규칙
- [../../../../conf/README.md](../../../../conf/README.md) — generation.yaml 설정
