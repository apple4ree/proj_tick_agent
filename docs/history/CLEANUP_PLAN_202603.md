# Cleanup Plan — 공개 인터페이스 단순화

## 완료된 작업

### scripts/ 정리

**공개 유지 (scripts/):**
- `generate_strategy.py` — 전략 생성
- `review_strategy.py` — 전략 검토
- `backtest.py` — 단일 종목 백테스트
- `backtest_strategy_universe.py` — universe 백테스트
- `run_generate_review_backtest.sh` — e2e 런처

**internal 이동 (scripts/internal/):**
- `run_generation_worker.py/.sh` — 생성 worker
- `run_backtest_worker.py/.sh` — 백테스트 worker
- `submit_backtest_job.py/.sh` — job 제출
- `submit_generation_job.sh` — 생성 job 제출
- `run_local_stack.sh` — dev 스택
- `run_validation_tiers.sh` — validation tier
- `summarize_universe_results.py` — 결과 집계
- `collect_data.py` — 데이터 수집
- `visualize.py` — 시각화
- `viz_trading_diagnostics.py` — 진단 시각화 (루트에서 이동)

### conf/ 정리

**핵심 유지 (conf/):**
- `app.yaml`, `paths.yaml`, `generation.yaml` — 앱/경로/생성 설정
- `backtest_base.yaml`, `backtest_worker.yaml`, `workers.yaml` — 백테스트/워커 설정
- `profiles/` — 환경별 프로필 (dev/smoke/prod)

**advanced 이동 (conf/advanced/):**
- `backtest_core.yaml` — qlib-style 상세 설정
- `baseline.yaml`, `baseline_mini.yaml` — 실험 baseline
- `env_config.yaml`, `train_config.yaml` — RL 레거시 (미사용)
- `EXPERIMENT_PROTOCOL.md` — 실험 규칙

### 루트 정리

**삭제/이동 완료:**
- `env_config.yaml` → `conf/advanced/` (루트 중복)
- `train_config.yaml` → `conf/advanced/` (루트 중복)
- `viz_trading_diagnostics.py` → `scripts/internal/`

### 문서 갱신

- `README.md` — 4 golden paths 중심으로 축소
- `docs/COMMANDS.md` — Core vs Internal 구분
- `scripts/README.md` — 공개 5개 + internal 목록
- `conf/README.md` — config stack + advanced/ 분리
- `tests/README.md` — smoke/stronger tier 중심
- `PROJECT.md`, `PIPELINE.md` — 경로 갱신

## 인터페이스 요약

### 공개 명령 (사용자가 직접 실행)
1. `scripts/generate_strategy.py` — 전략 생성
2. `scripts/review_strategy.py` — 전략 검토
3. `scripts/backtest.py` — 단일 종목 백테스트
4. `scripts/backtest_strategy_universe.py` — universe 백테스트
5. `scripts/run_generate_review_backtest.sh` — e2e 일괄 실행

### 기본 옵션 강조
- Backend: `openai` (live 권장) / `template` (테스트용)
- Mode: `live` (실제 API) / `mock` (fixture)
- Profile: `dev` / `smoke` / `prod`
