# strategy_loop/ — 코드 전략 탐색 루프

LLM이 Python 전략 코드를 반복 생성하고, 백테스트 피드백으로 개선하는 루프.

## 모듈 구성

| 파일 | 클래스/함수 | 역할 |
|------|-----------|------|
| `code_sandbox.py` | `exec_strategy_code()` | 생성 코드 안전 실행 (AST 검증 포함) |
| `hard_gate.py` | `validate_code()` | 백테스트 전 코드 검증 |
| `code_strategy.py` | `CodeStrategy` | Strategy ABC 구현 (generate_signal 래핑) |
| `distribution_filter.py` | `check_code_entry_frequency()` | 사전 진입 빈도 필터 |
| `threshold_optimizer.py` | `optimize_code_thresholds()` | UPPER_CASE 상수 Optuna 최적화 |
| `prompt_builder.py` | `build_code_generation_messages()`, `build_code_feedback_messages()` | LLM 메시지 구성 |
| `feedback_generator.py` | `FeedbackGenerator` | 백테스트 결과 → LLM 피드백 |
| `memory_store.py` | `MemoryStore` | 코드/결과/인사이트 저장 |
| `loop_runner.py` | `LoopRunner` | 메인 루프 |

## code_sandbox.py

생성 코드에서 허용되지 않은 import/이름을 차단하고,
`generate_signal(features, position)` 함수를 추출한다.

## code_strategy.py — CodeStrategy

`Strategy.generate_signal(state: MarketState) → Signal | None`

- `extract_builtin_features(state)`로 피처 추출
- 코드 함수 `generate_signal(features, position)` 호출
- 반환 규약:
  - `1`: 진입
  - `-1`: 청산
  - `None`: 유지

## loop_runner.py — LoopRunner

```python
runner = LoopRunner(client, memory_dir, output_dir)
result = runner.run(
    research_goal, n_iterations, data_dir, symbols, date_ranges, cfg
)
```

각 이터레이션 기록:
`IterationRecord(iteration, run_id, strategy_name, code, gate_result, backtest_summary, feedback, skipped)`
