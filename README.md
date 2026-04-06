# proj_rl_agent

KRX 틱 데이터 기반 **LLM 자동 코드 전략 탐색** 워크스페이스.

LLM이 Python 전략 코드를 생성하고, Hard Gate/분포 필터를 통과한 코드만 백테스트한다.
백테스트 결과를 피드백으로 재주입해 반복 개선한다.

## Quick Start

```bash
cd /home/dgu/tick/proj_rl_agent

# smoke (mock LLM)
bash scripts/run_code_loop_smoke.sh

# live (OpenAI API 필요)
export OPENAI_API_KEY=sk-...
bash scripts/run_code_loop_live.sh

# 단일 코드 전략 백테스트
PYTHONPATH=src python scripts/backtest.py \
  --code-file path/to/strategy.py \
  --symbol 005930 --start-date 20260313
```

## 핵심 디렉토리

| 디렉토리 | 역할 |
|----------|------|
| `scripts/` | CLI 진입점 |
| `src/strategy_loop/` | 코드 전략 루프 (생성→게이트→필터→백테스트→피드백→메모리) |
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
| `scripts/run_strategy_loop.py` | 코드 전략 반복 탐색 (주 진입점) |
| `scripts/run_code_loop_live.sh` | live 코드 루프 실행 래퍼 |
| `scripts/run_code_loop_smoke.sh` | mock smoke 실행 래퍼 |
| `scripts/backtest.py` | 단일 코드 전략 백테스트 |

## 산출물 (git 미추적)

`outputs/memory*/strategies/{run_id}.json` — 전략 코드 + 백테스트 요약 + LLM 피드백
`outputs/memory*/global_memory.json` — 교차 인사이트 / 실패 패턴
`outputs/backtests*/{run_id}/` — summary / CSV / plots

`outputs/`는 `.gitignore` 대상이며 코드가 자동 생성한다.

## 현재 한계

- 단일 종목 중심 백테스트 (대규모 universe sweep 미지원)
- production OMS / live trading 연결 없음
