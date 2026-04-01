# src/ — 소스 코드

## 하위 디렉토리

| 디렉토리 | Layer | 역할 |
|----------|-------|------|
| `strategy_loop/` | — | LLM 전략 탐색 루프 (주 진입점) |
| `data/` | 0 | KRX 틱 데이터 적재/정제/피처/MarketState |
| `strategy_block/` | — | Strategy ABC + BUILTIN_FEATURES |
| `execution_planning/` | 1~4 | Signal → 주문 실행 계획 |
| `market_simulation/` | 5 | LOB 기반 체결 시뮬레이션 |
| `evaluation_orchestration/` | 6~7 | 메트릭 계산 + 백테스트 파이프라인 |
| `monitoring/` | — | 이벤트 버스 + 검증 레이어 |
| `utils/` | — | config 로더, 로깅 |

`PYTHONPATH=src`로 실행.
