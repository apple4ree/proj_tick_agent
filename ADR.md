# 설계 의사결정 기록 (ADR)

---

## ADR-001: RL → LLM 자동 전략 탐색 플랫폼 전환

**상태**: 채택됨

### 맥락

초기 프로젝트는 PPO 기반 RL 에이전트 구조였다.

### 결정

LLM이 JSON 전략 스펙을 반복 생성하고, 백테스트 피드백으로 개선하는 루프로 전환.

| 기존 | 신규 |
|------|------|
| RL이 매 틱마다 행동 결정 | LLM이 전략 스펙 생성 |
| 단일 에이전트 학습/평가 | 생성 → 게이트 → 백테스트 → 피드백 루프 |
| latency 미고려 | latency를 기본 실험 변인으로 포함 |

### 근거

- **해석 가능성**: JSON 스펙은 사람이 읽고 수정 가능
- **빠른 탐색**: 학습 없이 즉시 백테스트
- **재현성**: 동일 스펙 → 동일 백테스트 결과
- **인프라 재활용**: Layer 0~7 파이프라인을 그대로 사용

---

## ADR-002: Simple JSON Spec (AST 제거)

**상태**: 채택됨

### 맥락

이전에는 복잡한 AST 기반 `StrategySpecV2` 포맷을 사용했다.

### 결정

AST를 제거하고 단순 JSON 딕셔너리 스펙을 채택.

```json
{
  "entry": {"side": "long", "condition": {...}, "size": 10},
  "exit": {"condition": {...}},
  "risk": {"max_position": 100}
}
```

조건식은 70줄짜리 재귀 `evaluate()` 함수로 평가 (`spec_simple.py`).

### 근거

- LLM이 생성하기 쉬운 포맷
- Hard Gate 검증이 단순해짐
- 피드백 루프에서 스펙 비교가 용이
- 컴파일러/리뷰어 등 중간 레이어 불필요

---

## ADR-003: Hard Gate — 백테스트 전 사전 검증

**상태**: 채택됨

### 결정

LLM 생성 스펙을 백테스트하기 전에 `HardGate.validate()`로 검증.
실패 시 백테스트를 건너뛰고 즉시 재생성.

### 근거

- 잘못된 피처명/연산자로 백테스트가 예외 없이 무의미한 결과를 내는 것을 방지
- 반복 루프에서 백테스트 비용(~95s)을 절약

---

## ADR-004: 두 단계 Memory

**상태**: 채택됨

### 결정

- `strategies/{run_id}.json`: 이터레이션별 스펙 + 결과 + 피드백 기록
- `global_memory.json`: 전략 간 교차 인사이트 (LLM이 생성, append 방식)

### 근거

- per-strategy 기록은 재현성과 디버깅에 사용
- global memory는 다음 이터레이션 프롬프트에 주입해 중복 탐색 방지

---

## ADR-005: positions_history 60틱 샘플링

**상태**: 채택됨

### 맥락

`pipeline_runner.py`에서 매 틱마다 positions_history를 append하면 89,724개 항목이 쌓여 turnover 계산이 382s 소요.

### 결정

60틱마다 1회 샘플링 → ~1,496개 항목.

### 결과

백테스트 report 단계: 382s → 95s.

---

## ADR-006: OpenAI mock 모드 — 시스템 프롬프트로 spec/feedback 구분

**상태**: 채택됨

### 맥락

mock 클라이언트가 spec 생성 요청인지 피드백 요청인지를 user 메시지 내용으로 구분하면,
previous_feedback이 포함된 generation prompt에서 오탐이 발생했다.

### 결정

시스템 프롬프트에 `"verdict"`와 `"issues"` 키워드가 모두 있으면 피드백 요청으로 판단.

```python
system = next((m["content"] for m in messages if m.get("role") == "system"), "")
if "verdict" in system and "issues" in system:
    return json.dumps(_MOCK_FEEDBACK)
return json.dumps(_MOCK_SPEC)
```
