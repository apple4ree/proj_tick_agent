# PIPELINE.md — 현재 파이프라인 (code-only)

## End-to-End Flow

```
research_goal
  → [1] LLM 코드 전략 생성
  → [2] Hard Gate (코드 검증)
  → [3] Distribution Filter (entry frequency)
  → [4] 백테스트 (Layer 0~7)
  → [5] LLM 피드백 생성
  → [6] Memory/RAG 저장
  → [1] 재생성 (retry) 또는 종료 (pass / n_iter 도달)
```

## 1) LLM 코드 전략 생성

진입점: `scripts/run_strategy_loop.py` → `LoopRunner.run()`

- `PromptBuilder.build_code_generation_messages(...)`로 messages 구성
- `OpenAIClient.chat_code(messages)` → Python code 반환
- `mode=mock`: `_MOCK_CODE` 반환 (API 호출 없음)

## 2) Hard Gate

`src/strategy_loop/hard_gate.py` — `validate_code(code) → HardGateResult`

검증 항목:
- 코드 비어있지 않은지
- AST 안전성 (`validate_ast`)
- sandbox 실행 가능 여부
- `generate_signal(features, position)` callable 존재 여부

게이트 실패 시 이터레이션 skip (백테스트 없이 재생성).

## 3) Distribution Filter

`src/strategy_loop/distribution_filter.py` — `check_code_entry_frequency(code, states)`

- 샘플 상태에서 `generate_signal(...) == 1` 빈도 계산
- 기본 허용 범위: `0.001 <= entry_frequency <= 0.50`
- 범위 밖이면 백테스트 전에 즉시 reject

## 4) 백테스트

`PipelineRunner.run(config, strategy, states)`

전략 구현체: `CodeStrategy`

산출물 (`outputs/backtests*/{run_id}/`):
- `summary.json` — net_pnl, sharpe, mdd, fill_rate 등 핵심 지표
- `realism_diagnostics.json` — 상세 aggregate
- `signals.csv`, `orders.csv`, `fills.csv`, `pnl_series.csv`, `market_quotes.csv`
- plots

## 5) LLM 피드백

`src/strategy_loop/feedback_generator.py`

- backtest summary를 프롬프트에 주입
- LLM 응답: `{"issues": [...], "suggestions": [...], "verdict": "pass|retry|fail"}`
- `mode=mock`: `_MOCK_FEEDBACK` 반환

## 6) Memory 저장

`src/strategy_loop/memory_store.py`

- `outputs/memory*/strategies/{run_id}.json` — `strategy_name + code + backtest_summary + feedback`
- `outputs/memory*/global_memory.json` — `insights`, `failure_patterns`

## Loop 종료 조건

| 조건 | 동작 |
|------|------|
| `verdict=pass` + OOS 통과 | 루프 종료, `best_run_id` 기록 |
| `verdict=pass` + OOS 미설정 | 루프 종료 |
| 그 외 | 다음 이터레이션 |
| `n_iterations` 도달 | 루프 종료 |

## 공개 CLI

| 스크립트 | 주요 옵션 |
|---------|----------|
| `run_strategy_loop.py` | `--research-goal`, `--symbols/--symbol`, `--is-start`, `--is-end`, `--mode`, `--model`, `--n-iter`, `--memory-dir`, `--output-dir`, `--config`, `--profile` |
| `backtest.py` | `--code-file`, `--symbol`, `--start-date`, `--end-date`, `--config`, `--profile` |
