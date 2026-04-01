# Project Map

## Root Layout

| 경로 | 역할 |
|------|------|
| `scripts/` | CLI 진입점 |
| `src/` | 소스 코드 |
| `conf/` | YAML 설정 |
| `tests/` | pytest suite |
| `outputs/` | 런타임 산출물 (git 미추적) |

## `src/` 서브트리

```
src/
├── strategy_loop/               # LLM 전략 탐색 루프 (핵심)
│   ├── spec_simple.py           # JSON 스펙 포맷 + recursive evaluate()
│   ├── hard_gate.py             # 백테스트 전 스펙 검증
│   ├── simple_spec_strategy.py  # Strategy ABC 구현
│   ├── openai_client.py         # OpenAI 래퍼 (live/mock)
│   ├── prompt_builder.py        # LLM 메시지 구성
│   ├── feedback_generator.py    # 백테스트 결과 → LLM 피드백
│   ├── memory_store.py          # 전략 기록 저장
│   └── loop_runner.py           # 메인 루프
│
├── data/
│   └── layer0_data/             # 틱 데이터 적재/정제/피처/MarketState
│
├── execution_planning/
│   ├── layer1_signal/           # Signal 데이터 계약
│   ├── layer2_position/         # Signal → TargetPosition
│   ├── layer3_order/            # TargetPosition → ParentOrder
│   └── layer4_execution/        # ParentOrder → ChildOrder
│
├── market_simulation/
│   └── layer5_simulator/        # ChildOrder → FillEvent (LOB 매칭)
│
├── evaluation_orchestration/
│   ├── layer6_evaluator/        # PnL/Risk/Execution/Turnover 메트릭
│   └── layer7_validation/       # PipelineRunner + ReportBuilder
│
├── strategy_block/
│   ├── strategy/base.py         # Strategy ABC
│   └── strategy_compiler/v2/features.py  # BUILTIN_FEATURES 목록
│
├── monitoring/                  # 이벤트 버스 + 검증 레이어
└── utils/                       # config, logger, metrics
```

## `scripts/`

| 파일 | 역할 |
|------|------|
| `run_strategy_loop.py` | LLM 반복 전략 탐색 루프 (주 진입점) |
| `backtest.py` | 단일 종목 백테스트 |

## `conf/`

| 파일 | 역할 |
|------|------|
| `app.yaml` | 앱 이름, env, log_level |
| `paths.yaml` | data_dir, outputs_dir |
| `backtest_base.yaml` | 백테스트 기본 파라미터 |
| `backtest_worker.yaml` | latency sweep 설정 |
| `generation.yaml` | 전략 생성 설정 |
| `profiles/` | dev / smoke / prod 프로필 오버라이드 |

## `outputs/` 구조 (런타임 자동 생성)

```
outputs/
├── memory/
│   ├── strategies/{run_id}.json   # 스펙 + 백테스트 요약 + 피드백
│   └── global_memory.json         # 전략 간 교차 인사이트
└── backtests/{run_id}/
    ├── summary.json
    ├── fills.csv, signals.csv, orders.csv, pnl_series.csv, market_quotes.csv
    └── plots/
```
