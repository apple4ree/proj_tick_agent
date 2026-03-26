# scripts/

## 공개 CLI (5개)

| 스크립트 | 용도 |
|---------|------|
| `generate_strategy.py` | 전략 생성 (`--backend openai/template`, `--direct`로 즉시 실행) |
| `review_strategy.py` | 전략 정적 검토 |
| `backtest.py` | 단일 종목 백테스트 |
| `backtest_strategy_universe.py` | 전종목 × 다 latency 백테스트 |
| `run_generate_review_backtest.sh` | 생성 → 검토 → 백테스트 일괄 실행 |

## internal/ 구조

### workers/ — Worker 프로세스

| 스크립트 | 용도 |
|---------|------|
| `run_generation_worker.py/.sh` | 생성 job worker (polling daemon) |
| `run_backtest_worker.py/.sh` | 백테스트 job worker (polling daemon) |
| `run_local_stack.sh` | generation + backtest worker 동시 기동 |

### ops/ — 운영 도구

| 스크립트 | 용도 |
|---------|------|
| `submit_backtest_job.py/.sh` | 백테스트 job을 FileQueue에 제출 |
| `submit_generation_job.sh` | generate_strategy.py 래퍼 |
| `run_validation_tiers.sh` | smoke/stronger validation tier 실행 |

### adhoc/ — 데이터 수집/시각화/집계

| 스크립트 | 용도 |
|---------|------|
| `visualize.py` | 백테스트 결과 시각화 (5종 plot) |
| `viz_trading_diagnostics.py` | 실험 디렉토리 진단 시각화 |
| `summarize_universe_results.py` | universe_results.csv 집계 |
| `collect_data.py` | KIS H0STASP0 틱 데이터 수집 |

## 주의사항

- 모든 Python 스크립트는 `PYTHONPATH=src`가 필요함
- `--profile`과 `--config`로 설정 override 가능
- Worker는 `--once` 플래그로 단일 job 처리 후 종료 가능
