# strategy_review/ — v2 전략 정적 검토

StrategySpecV2를 정적 규칙으로 점검하여 구조적 오류와 논리적 문제를 탐지한다. 현재 **reviewer_v2** 중심이다.

## 핵심 역할

- Phase 1~3에 걸쳐 20개 체크 규칙 적용
- Hard gate(error) vs Warning vs Info 3단계 심각도 구분
- 생성 파이프라인의 필수 게이트 (error 시 생성 차단)
- 알려진 피처 목록 기반 참조 검증

## 대표 파일

| 파일 | 역할 |
|------|------|
| `v2/reviewer_v2.py` | `StrategyReviewerV2` — 20개 체크 규칙 구현 |
| `review_common.py` | `ReviewIssue`, `ReviewResult`, `KNOWN_FEATURES` 공통 타입 |

## Hard Gate vs Warning

| 심각도 | 동작 | 예시 |
|--------|------|------|
| **error** | 생성/실행 차단 | 스키마 위반, 필수 필드 누락 |
| **warning** | 로그에 기록, 차단 안 함 | dead regime, 과도한 cooldown, 논리 모순 |
| **info** | 참고 정보 | 미지원 피처 참조 |

## 체크 규칙 (20개)

**Phase 1 (기본):**
1. schema — StrategySpecV2 구조 검증
2. expression_safety — AST 깊이 > 10 경고
3. feature_availability — 알 수 없는 피처 참조 (info)
4. logical_contradiction — 불가능한 조건 조합 (AND 내 충돌)
5. unreachable_entry — cooldown > 10000 ticks 경고
6. risk_inconsistency — inventory_cap < max_position 등
7. exit_completeness — close_all exit 규칙 부재 경고

**Phase 2 (Regime):**
8. dead_regime — 활성화 불가 regime 경고
9. regime_reference_integrity — entry/exit policy 참조 존재 확인
10. execution_risk_mismatch — execution/risk policy 호환성
11. latency_structure_warning — latency hint 불일치

**Phase 3 (State/Degradation):**
12. state_reference_integrity — guard/event가 정의된 변수 참조
13. state_deadlock — guard가 모든 진입 차단 + 해제 이벤트 없음
14. guard_conflict — 복수 guard 동시 충족 불가
15. degradation_conflict — degradation rule 충돌
16. exit_semantics_risk — degraded 상태에서 exit 의미
17. position_attr_sanity — position_attr 사용 정합성
18. state_event_order_risk — event 순서 문제 (entry before exit)
19. execution_override_conflict — execution override 충돌
20. regime_exit_coverage — 모든 regime의 exit 정책 커버리지

## Static / Heuristic 성격

- 모든 규칙은 **코드 수준 정적 분석**이며 런타임 시뮬레이션 없이 판단
- 일부 체크(dead_regime, logical_contradiction)는 **간단한 heuristic 분석**으로 모든 경우를 탐지하지 못할 수 있음
- LLM 기반 soft review는 제거됨 (ADR-019). 현재 정적 규칙만 적용

## 주의사항

- `KNOWN_FEATURES`(~25종)에 없는 피처는 info로 보고되지만 차단되지 않음
- 새 Phase의 기능 추가 시 reviewer_v2.py에 대응 체크 추가 필요
- ReviewResult.passed는 error 심각도 이슈가 0개일 때 True

## 관련 문서

- [../strategy_specs/README.md](../strategy_specs/README.md) — 검토 대상 스키마
- [../strategy_generation/README.md](../strategy_generation/README.md) — 생성 시 자동 리뷰
