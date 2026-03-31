# scripts/

이 문서는 **public CLI current surface**를 설명한다. 옵션 변경/의미 변경 시 이 문서를 먼저 갱신한다.

## Public CLI (Canonical)

| 스크립트 | 용도 | Public 옵션(surface) |
|---|---|---|
| `generate_strategy.py` | 전략 생성 (`template/openai`) | `--goal`, `--backend`, `--config`, `--profile`, `--direct` |
| `review_strategy.py` | 전략 리뷰 (`static/llm-review/auto-repair`) | `spec_path`, `--mode`, `--config`, `--profile` |
| `backtest.py` | 단일 종목 백테스트 | `--spec`, `--symbol`, `--start-date`, `--end-date`, `--config`, `--profile` |
| `backtest_strategy_universe.py` | universe 백테스트 | `--spec`, `--data-dir`, `--start-date`, `--end-date`, `--config`, `--profile` |
| `evaluate_walk_forward.py` | rolling window 검증 + selection decision | `--spec`, `--symbol`/`--universe`, `--start-date`, `--end-date`, `--profile`, `--selection-config`, `--trial-id` |
| `promote_candidate.py` | promotion gate 평가 + handoff bundle export | `--spec`, `--walk-forward-report`, `--trial-id`, `--promotion-config`, `--profile`, `--out-dir` |
| `run_generate_review_backtest.sh` | 생성 → 리뷰 → 백테스트 일괄 실행 | `--goal`, `--symbol`, `--universe`, `--start-date`, `--end-date`, `--backend`, `--review-mode`, `--config`, `--profile` |
| `run_generate_review_backtest_batch.sh` | universe 다중 goal 순차 배치 실행 | `--goals-file`, `--start-date`, `--end-date`, `--profile`, `--config`, `--backend`, `--review-mode`, `--continue-on-error`, `--fail-fast`, `--out-dir` |

runtime/LLM 세부 옵션(`model`, `client_mode`, `auto_approve`, artifact 상세 경로 정책)은 config(`conf/generation.yaml` 등)에서 관리한다.

## Review Mode Semantics

`review_strategy.py --mode`:
- `static`: static review만 수행 (artifact 저장 없음)
- `llm-review`: static + llm critique (artifact 자동 저장)
- `auto-repair`: static + llm + constrained repair + static re-review (artifact 자동 저장)

최종 통과 기준은 항상 static reviewer 결과다.

## Wrapper Runtime Behavior

`run_generate_review_backtest.sh`:
- generation/review/backtest stdout/stderr를 실시간으로 터미널에 출력하고, 동시에 `/tmp/proj_gen_e2e.log`, `/tmp/proj_review_e2e.log`, `/tmp/proj_backtest_e2e.log`에 저장
- 실패 시 마지막 로그와 핵심 key-value (`GENERATED_SPEC`, `REVIEW_STATUS`, `ARTIFACT_DIR`)를 기준으로 즉시 요약
- `--review-mode` 기본값은 `static`이며, `llm-review` / `auto-repair`를 shell에서 직접 선택 가능
- `--review-mode auto-repair`에서 `repaired_spec.json`이 존재하면 해당 spec를 backtest에 사용
- `--backend openai` + resolved generation mode가 `live`이면 `OPENAI_API_KEY`가 필요

## Batch Wrapper Behavior

`run_generate_review_backtest_batch.sh`:
- 기존 단일 래퍼(`run_generate_review_backtest.sh`)를 재사용해 goal 목록을 universe 모드로 순차 실행
- `--goals-file` 포맷: 한 줄당 goal 1개, 빈 줄/`#` 주석 줄은 무시
- 기본 산출 경로: `outputs/batch_runs/<timestamp>` (`--out-dir`로 override 가능)
- goal별 로그: `<out-dir>/logs/001_<slug>.log`
- 배치 요약: `<out-dir>/summary.csv`, `<out-dir>/summary.md`, `<out-dir>/meta.json`
- 권장 preset 경로: `conf/goals/universe_goals_smoke.txt`, `conf/goals/universe_goals_core.txt`, `conf/goals/universe_goals_openai.txt`

예시 (smoke sanity-check):
```bash
./scripts/run_generate_review_backtest_batch.sh \
  --goals-file conf/goals/universe_goals_smoke.txt \
  --start-date 2026-03-13 \
  --end-date 2026-03-13 \
  --profile smoke \
  --backend template \
  --review-mode static
```

예시 (core 탐색):
```bash
./scripts/run_generate_review_backtest_batch.sh \
  --goals-file conf/goals/universe_goals_core.txt \
  --start-date 2026-03-13 \
  --end-date 2026-03-13 \
  --profile dev \
  --backend template \
  --review-mode static
```

## Walk-Forward CLI Semantics

`evaluate_walk_forward.py`:
- 기존 backtest artifact(`summary.json`, `realism_diagnostics.json`)를 window별로 재사용
- `selection_metrics`로 run score 계산
- `WalkForwardSelector`로 pass/fail aggregate decision 계산
- 출력 key:
  - `WALK_FORWARD_STATUS=PASSED|FAILED`
  - `WALK_FORWARD_REPORT=<.../walk_forward_report.json>`
  - `WALK_FORWARD_OUTDIR=<...>`

## Promotion CLI Semantics

`promote_candidate.py`:
- walk-forward report + trial metadata를 기반으로 deterministic promotion gate를 평가
- pass 시 deployment contract/export bundle 생성
- 출력 key:
  - `PROMOTION_STATUS=PASSED|FAILED`
  - `PROMOTION_BUNDLE=<...>` (passed 시)
  - `PROMOTION_REASONS=<...>`

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
| `visualize.py` | 백테스트 결과 static plot 생성 (전체 8종 수동 생성) |
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

`intraday_cumulative_profit.png`는 intraday cumulative PnL line chart에 더해,
`summary.json` 기반 핵심 성과 지표(Net PnL/Sharpe/Max DD/Fill·Cancel Rate)를 하단 text box로 표시한다.
`summary.json`이 없으면 `Summary metrics unavailable`로 degraded 표시를 유지한다.

자동 백테스트 경로(`backtest.py`, `backtest_strategy_universe.py`)는 기본적으로
`dashboard.png`, `intraday_cumulative_profit.png`, `trade_timeline.png` 세 개를 생성한다.

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
