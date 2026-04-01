# proj_rl_agent

KRX 틱 데이터 기반 **LLM 자동 전략 탐색** 워크스페이스.

LLM이 JSON 전략 스펙을 생성하고, Hard Gate를 통과한 스펙만 백테스트한다.
백테스트 결과를 피드백으로 LLM에 주입해 반복 개선한다.

## Quick Start

```bash
cd /home/dgu/tick/proj_rl_agent

# mock 모드 (LLM 없이 테스트, ~138s/iter)
PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "order imbalance momentum" \
    --symbol 005930 --start-date 20260313 \
    --mode mock --n-iter 3

# 실제 OpenAI 사용
OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "spread mean reversion" \
    --symbol 005930 --start-date 20260313 --end-date 20260314 \
    --mode live --model gpt-4o --n-iter 10

# 단일 종목 백테스트 (spec JSON 직접 실행)
PYTHONPATH=src python scripts/backtest.py \
    --spec outputs/memory/strategies/abc123.json \
    --symbol 005930 --start-date 20260313
```

## 핵심 디렉토리

| 디렉토리 | 역할 |
|----------|------|
| `scripts/` | CLI 진입점 |
| `src/strategy_loop/` | LLM 전략 루프 (생성→게이트→백테스트→피드백→메모리) |
| `src/data/` | KRX 틱 데이터 적재/정제/피처 계산 |
| `src/execution_planning/` | Signal→주문 실행 계획 (Layer 1~4) |
| `src/market_simulation/` | LOB 기반 체결 시뮬레이션 (Layer 5) |
| `src/evaluation_orchestration/` | 메트릭 계산 + 백테스트 파이프라인 (Layer 6~7) |
| `src/monitoring/` | 이벤트 버스 + 검증 레이어 |
| `conf/` | YAML 설정 (프로필 포함) |
| `outputs/` | 런타임 산출물 (git 미추적) |

## CLI

| 스크립트 | 용도 |
|---------|------|
| `scripts/run_strategy_loop.py` | LLM 반복 전략 탐색 (주 진입점) |
| `scripts/backtest.py` | 단일 종목 백테스트 |

## 산출물 (git 미추적)

`outputs/memory/strategies/{run_id}.json` — 전략 스펙 + 백테스트 요약 + LLM 피드백  
`outputs/memory/global_memory.json` — 전략 간 교차 인사이트  
`outputs/backtests/{run_id}/` — summary.json / CSVs / plots  

`outputs/`는 `.gitignore` 대상이며 코드가 자동 생성한다.

## 현재 한계

- 단일 종목 백테스트 (universe sweep 미지원)
- production OMS / live trading 연결 없음
