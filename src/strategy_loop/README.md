# strategy_loop/ — LLM 전략 탐색 루프

LLM이 JSON 전략 스펙을 반복 생성하고, 백테스트 피드백으로 개선하는 루프.

## 모듈 구성

| 파일 | 클래스/함수 | 역할 |
|------|-----------|------|
| `spec_simple.py` | `evaluate()` | 조건 노드 재귀 평가 |
| `hard_gate.py` | `HardGate`, `validate()` | 백테스트 전 스펙 검증 |
| `simple_spec_strategy.py` | `SimpleSpecStrategy` | Strategy ABC 구현 |
| `openai_client.py` | `OpenAIClient` | OpenAI 래퍼 (live/mock) |
| `prompt_builder.py` | `build_generation_messages()`, `build_feedback_messages()` | LLM 메시지 구성 |
| `feedback_generator.py` | `FeedbackGenerator` | 백테스트 결과 → LLM 피드백 |
| `memory_store.py` | `MemoryStore` | 전략 기록 저장 |
| `loop_runner.py` | `LoopRunner` | 메인 루프 |

## spec_simple.py — 조건식 평가

```python
def evaluate(cond: dict, features: dict[str, float], position: dict[str, float]) -> bool
```

노드 타입:
- `comparison`: `feature`/`position_attr`/`const` 두 값을 `op`로 비교
- `any`: 하위 조건 중 하나라도 True
- `all`: 하위 조건 모두 True
- `not`: 하위 조건 반전

`position_attr` 지원 키: `holding_ticks`, `size`, `side`

## hard_gate.py — HardGate

```python
result = validate(spec)  # HardGateResult(passed, errors)
```

검증 항목:
- 필수 키: `name`, `entry`, `exit`, `risk`
- `entry.side` ∈ `{long, short}`
- `entry.size > 0`, `risk.max_position > 0`
- 조건식 피처명 ∈ `BUILTIN_FEATURES`
- `op` ∈ `{>, <, >=, <=, ==, !=}`

## simple_spec_strategy.py — SimpleSpecStrategy

`Strategy.generate_signal(state: MarketState) → Signal | None`

- `extract_builtin_features(state)`로 피처 추출
- `_in_position=False`: entry 조건 평가 → Signal(side, size)
- `_in_position=True`: exit 조건 평가 → Signal(FLAT) 또는 None
- `_holding_ticks`: 매 틱 증가, position_attr `holding_ticks`로 노출

## openai_client.py — OpenAIClient

```python
client = OpenAIClient(model="gpt-4o", mode="live")   # 실제 API
client = OpenAIClient(mode="mock")                    # 테스트용
```

mock 모드: 시스템 프롬프트에 `"verdict"`와 `"issues"` 키워드가 모두 있으면 `_MOCK_FEEDBACK`,
그렇지 않으면 `_MOCK_SPEC` 반환.

## loop_runner.py — LoopRunner

```python
runner = LoopRunner(client, memory_dir, output_dir)
result = runner.run(
    research_goal, n_iterations, data_dir, symbol, start_date, end_date, cfg
)
# result: LoopResult(iterations, best_run_id, verdict)
```

각 이터레이션 기록: `IterationRecord(iteration, run_id, spec, gate_result, backtest_summary, feedback, skipped)`
