# PIPELINE.md — 현재 파이프라인

## End-to-End Flow

```
research_goal
  → [1] LLM 전략 생성
  → [2] Hard Gate (사전 검증)
  → [3] 백테스트 (Layer 0~7)
  → [4] LLM 피드백 생성
  → [5] Memory 저장
  → [1] 재생성 (retry) 또는 종료 (pass / fail / n_iter 도달)
```

## 1) LLM 전략 생성

진입점: `scripts/run_strategy_loop.py` → `LoopRunner.run()`

- `PromptBuilder.build_generation_messages(research_goal, previous_feedback)`로 messages 구성
- `OpenAIClient.chat_json(messages)` → JSON spec dict 반환
- `mode=mock`: `_MOCK_SPEC` 반환 (API 호출 없음, 시스템 프롬프트로 spec/feedback 구분)

## 2) Hard Gate

`src/strategy_loop/hard_gate.py` — `validate(spec) → HardGateResult`

검증 항목:
- 필수 키 존재: `name`, `entry`, `exit`, `risk`
- `entry.side` ∈ `{long, short}`
- `entry.size > 0`, `risk.max_position > 0`
- 조건식 재귀 순회: `feature` 키가 있으면 `BUILTIN_FEATURES` 내에 존재해야 함
- 연산자 `op` ∈ `{>, <, >=, <=, ==, !=}`

게이트 실패 시 이터레이션 skip (백테스트 없이 재생성).

## 3) 백테스트

`PipelineRunner.run(config, strategy, states)`

핵심 realism:
- `market_data_delay_ms` — observation lag (`observed_state` 분리)
- `decision_compute_ms` — 결정 지연
- `latency.order_submit_ms` / `order_ack_ms` / `cancel_ms` — venue latency
- 5종 대기열 모델

산출물 (`outputs/backtests/{run_id}/`):
- `summary.json` — net_pnl, sharpe, mdd, fill_rate 등 핵심 지표
- `realism_diagnostics.json` — 상세 aggregate
- `signals.csv`, `orders.csv`, `fills.csv`, `pnl_series.csv`, `market_quotes.csv`
- `plots/dashboard.png`, `intraday_cumulative_profit.png`, `trade_timeline.png`

## 4) LLM 피드백

`src/strategy_loop/feedback_generator.py`

- `summary.json` 내용을 프롬프트에 주입
- LLM 응답: `{"issues": [...], "suggestions": [...], "verdict": "pass|retry|fail"}`
- `mode=mock`: `_MOCK_FEEDBACK` 반환

## 5) Memory 저장

`src/strategy_loop/memory_store.py`

- `outputs/memory/strategies/{run_id}.json` — spec + backtest_summary + feedback
- `outputs/memory/global_memory.json` — 전략 간 교차 인사이트 (append)

## Loop 종료 조건

| 조건 | 동작 |
|------|------|
| `verdict=pass` | 루프 종료, `best_run_id` 기록 |
| `verdict=fail` | 루프 종료 |
| `verdict=retry` | 다음 이터레이션 |
| `n_iterations` 도달 | 루프 종료 |

## 공개 CLI

| 스크립트 | 주요 옵션 |
|---------|----------|
| `run_strategy_loop.py` | `--research-goal`, `--symbol`, `--start-date`, `--end-date`, `--mode`, `--model`, `--n-iter`, `--memory-dir`, `--output-dir`, `--config`, `--profile` |
| `backtest.py` | `--spec`, `--symbol`, `--start-date`, `--end-date`, `--config`, `--profile` |

## Known Limitations

- 단일 종목 백테스트 (universe sweep 미지원)
- production OMS / live trading 연결 없음
- live 모드에서 동일 goal이라도 스펙이 달라질 수 있음 (mock 모드가 deterministic baseline)
