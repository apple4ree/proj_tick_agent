# scripts/

이 문서는 **public CLI current surface**를 설명한다. 옵션 변경/의미 변경 시 이 문서를 먼저 갱신한다.

## Public CLI (Canonical)

| 스크립트 | 용도 | Public 옵션(surface) |
|---|---|---|
| `generate_strategy.py` | 전략 생성 (`template/openai`) | `--goal`, `--backend`, `--config`, `--profile`, `--direct` |
| `review_strategy.py` | 전략 리뷰 (`static/llm-review/auto-repair`) | `spec_path`, `--mode`, `--config`, `--profile` |
| `backtest.py` | 단일 종목 백테스트 | `--spec`, `--symbol`, `--start-date`, `--end-date`, `--config`, `--profile` |
| `backtest_strategy_universe.py` | universe 백테스트 | `--spec`, `--data-dir`, `--start-date`, `--end-date`, `--config`, `--profile` |
| `run_generate_review_backtest.sh` | 생성 → 리뷰 → 백테스트 일괄 실행 | `--goal`, `--symbol`, `--universe`, `--start-date`, `--end-date`, `--backend`, `--config`, `--profile` |

runtime/LLM 세부 옵션(`model`, `client_mode`, `auto_approve`, artifact 상세 경로 정책)은 config(`conf/generation.yaml` 등)에서 관리한다.

## Review Mode Semantics

`review_strategy.py --mode`:
- `static`: static review만 수행 (artifact 저장 없음)
- `llm-review`: static + llm critique (artifact 자동 저장)
- `auto-repair`: static + llm + constrained repair + static re-review (artifact 자동 저장)

최종 통과 기준은 항상 static reviewer 결과다.

## Internal Scripts

### workers/

| 스크립트 | 용도 |
|---|---|
| `run_generation_worker.py` / `.sh` | generation queue worker |
| `run_backtest_worker.py` / `.sh` | backtest queue worker |
| `run_local_stack.sh` | generation+backtest worker 동시 실행 |

### ops/

| 스크립트 | 용도 |
|---|---|
| `submit_backtest_job.py` / `.sh` | backtest job 제출 |
| `submit_generation_job.sh` | generation submit wrapper |
| `run_validation_tiers.sh` | smoke/stronger validation tier |

### internal/adhoc/

| 스크립트 | 용도 |
|---|---|
| `visualize.py` | 백테스트 결과 static plot 생성 (8종) |
| `summarize_universe_results.py` | universe 결과 집계 |
| `collect_data.py` | KIS H0STASP0 데이터 수집 |
| `run_phase4_benchmark_freeze.py` | Phase 4 benchmark/freeze artifact 생성 |
| `compare_phase4_baselines.py` | candidate freeze artifact와 baseline drift 비교 |
| `viz_trading_diagnostics.py` | 실험 디렉토리 진단 시각화 |

## Visualization Output (Current)

`visualize.py` 주요 산출물:
- `overview.png`
- `signal_analysis.png`
- `execution_quality.png`
- `dashboard.png`
- `intraday_cumulative_profit.png`
- `trade_timeline.png`
- `equity_risk.png`
- `realism_dashboard.png`

## Related Canonical Docs

- `../PIPELINE.md`
- `../src/strategy_block/strategy_generation/README.md`
- `../src/strategy_block/strategy_review/README.md`
- `../src/evaluation_orchestration/layer7_validation/README.md`
- `../docs/analysis/benchmark_freeze_protocol.md`

## Notes

- Python 스크립트는 기본적으로 `PYTHONPATH=src` 환경에서 실행한다.
- current canonical behavior는 Tier 1 문서(README/PIPELINE) + Tier 2 freeze docs를 우선한다.
- historical analysis 문서는 배경 맥락으로만 사용한다.
