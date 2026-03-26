# Cleanup Final — 저장소 정리 최종 결과

## 1. 수정/이동한 파일

### visualize 경로 정합성 복구
- `src/evaluation_orchestration/layer7_validation/report_builder.py` — `from scripts.visualize import` → `importlib` 기반 `scripts/internal/adhoc/visualize.py` 로딩으로 변경
- `tests/test_visualize_intraday_plot.py` — `scripts/visualize.py` → `scripts/internal/adhoc/visualize.py`
- `tests/test_pnl_ledger_fixes.py` — `sys.path` 경로 `scripts/internal/adhoc/`로 갱신

### scripts/internal 재분류 (이동)

**workers/ (worker 프로세스):**
- `run_generation_worker.py`, `run_generation_worker.sh`
- `run_backtest_worker.py`, `run_backtest_worker.sh`
- `run_local_stack.sh`

**ops/ (운영 도구):**
- `submit_backtest_job.py`, `submit_backtest_job.sh`
- `submit_generation_job.sh`
- `run_validation_tiers.sh`

**adhoc/ (데이터 수집/시각화/집계):**
- `visualize.py`
- `viz_trading_diagnostics.py`
- `summarize_universe_results.py`
- `collect_data.py`

### 경로 갱신한 파일
- 모든 shell wrapper: `PROJECT_ROOT` 경로 `../../..`로 갱신, exec 경로 갱신
- 모든 Python docstring: 자기 참조 경로 갱신
- `docs/COMMANDS.md`, `scripts/README.md`, `tests/README.md`, `conf/README.md`
- `README.md`, `PROJECT.md`, `PIPELINE.md`
- `conf/advanced/EXPERIMENT_PROTOCOL.md`

### 캐시/산출물 정리
- `__pycache__/`, `*.pyc`, `.pytest_cache/` 전체 삭제
- `.gitignore`에 `jobs/` 추가

## 2. 비핵심 디렉토리 정리 (실행 완료)

| 디렉토리 | 조치 | 근거 |
|----------|------|------|
| `util/` | **삭제 완료** | src/tests/scripts에서 import 0건. 자기 참조(2건)만 존재. RL 시절 잔재. 복원 필요 시 git history에서 복구 가능 |
| `schemas/` | **삭제 완료** | 전역 import 0건. 현재 파이프라인 미사용. RL 시절 잔재. 복원 필요 시 git history에서 복구 가능 |
| `notebooks/` | **삭제 완료** | 빈 디렉토리 (.gitkeep만 존재). 코드 참조 없음 |
| `data/processed/` | **삭제 완료** | 빈 디렉토리. 코드 참조 없음 |
| `data/raw/` | **유지** | `collect_data.py`의 기본 출력 경로로 참조됨 |

## 3. Generated Artifacts 정리

| 디렉토리 | 조치 | 상태 |
|----------|------|------|
| `outputs/` (55MB) | 내용물 삭제, `.gitkeep`로 경로 유지 | `.gitignore`에 포함 |
| `logs/` | 내용물 삭제, `.gitkeep`로 경로 유지 | `.gitignore`에 포함 |
| `jobs/` | 내용물 삭제, `.gitkeep`로 경로 유지 | `.gitignore`에 포함 (신규 추가) |
| `experiments/` | 내용물 삭제, `.gitkeep`로 경로 유지 | `.gitignore`에 포함 |
| `checkpoints/` | 내용물 삭제, `.gitkeep`로 경로 유지 | `.gitignore`에 포함 |

정책: 이 디렉토리들은 런타임에 생성되는 산출물 경로이다. 코드가 이 경로를 기대하므로 디렉토리 자체는 유지하되, 내용물은 저장소 본체에 포함하지 않는다.

## 4. Internal 재분류 결과

```
scripts/
  generate_strategy.py          # 공개
  review_strategy.py            # 공개
  backtest.py                   # 공개
  backtest_strategy_universe.py # 공개
  run_generate_review_backtest.sh # 공개
  internal/
    workers/                    # worker 프로세스
      run_generation_worker.py/.sh
      run_backtest_worker.py/.sh
      run_local_stack.sh
    ops/                        # 운영 도구
      submit_backtest_job.py/.sh
      submit_generation_job.sh
      run_validation_tiers.sh
    adhoc/                      # 데이터/시각화/집계
      visualize.py
      viz_trading_diagnostics.py
      summarize_universe_results.py
      collect_data.py
```

## 5. 공개 진입점 최종 목록

| # | 스크립트 | 용도 |
|---|---------|------|
| 1 | `scripts/generate_strategy.py` | 전략 생성 (openai/template) |
| 2 | `scripts/review_strategy.py` | 전략 정적 검토 |
| 3 | `scripts/backtest.py` | 단일 종목 백테스트 |
| 4 | `scripts/backtest_strategy_universe.py` | 전종목 × 다 latency 백테스트 |
| 5 | `scripts/run_generate_review_backtest.sh` | 생성 → 검토 → 백테스트 일괄 실행 |

## 6. 검증 결과

| 검증 항목 | 결과 |
|----------|------|
| 공개 CLI 5개 `--help` | 전체 통과 |
| Smoke tier (11 tests) | 전체 통과 |
| Stronger tier (42 tests) | 전체 통과 |
| Visualize tests (3 tests) | 전체 통과 |
| Queue position tests (22 tests) | 전체 통과 |
| `util/`, `schemas/` 삭제 후 import 에러 | 없음 |
| Cache 잔존 | 0건 |

## 7. 추가 마감 작업 (완료)

- [x] `setup.sh` 삭제 — 다른 프로젝트(`lob-execution`) 대상 legacy bootstrapper, 현재 proj_rl_agent와 무관
- [x] `COMMIT_PLAN.md` → `docs/history/COMMIT_PLAN_202603.md` 이동 — 루트 표면 정리
- [x] `CLEANUP_PLAN.md` → `docs/history/CLEANUP_PLAN_202603.md` 이동
- [x] `.gitkeep` 제거 — artifact 디렉토리는 코드가 `mkdir(parents=True)` 로 자동 생성, `.gitkeep` 불필요
- [x] `.gitignore` 정책 주석 추가
