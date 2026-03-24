# scripts/ — 운영 CLI 및 런처

프로젝트의 모든 실행 진입점을 모은 디렉토리다. Python CLI 스크립트와 Shell 런처로 구분된다.

## 핵심 역할

- 전략 생성/검토/백테스트의 직접 실행 CLI 제공
- Worker 기반 비동기 실행 경로 제공
- End-to-end 파이프라인 런처 제공
- Validation tier(smoke/stronger) 실행

## Direct CLI vs Worker 경로

**Direct CLI**: 명령 즉시 실행. 소규모 실험/디버깅에 적합.
```bash
PYTHONPATH=src python scripts/generate_strategy.py --goal "..." --direct
PYTHONPATH=src python scripts/backtest.py --spec ... --symbol 005930
```

**Worker/Queue**: FileQueue에 job 제출 → worker가 polling하여 처리. 대규모/자동화에 적합.
```bash
bash scripts/submit_generation_job.sh "order imbalance alpha"
PYTHONPATH=src python scripts/run_generation_worker.py --once
```

## Python 스크립트

| 스크립트 | Block | 용도 |
|---------|-------|------|
| `generate_strategy.py` | Strategy | v2 전략 생성 (template/openai backend, `--direct`로 queue 우회) |
| `review_strategy.py` | Strategy | v2 spec 정적 검토 |
| `backtest.py` | Evaluation | 단일 종목 백테스트 |
| `backtest_strategy_universe.py` | Evaluation | 전종목 × 다 latency 백테스트 |
| `summarize_universe_results.py` | Evaluation | universe_results.csv 집계 |
| `collect_data.py` | Data | KIS H0STASP0 틱 데이터 수집 |
| `visualize.py` | Evaluation | 백테스트 결과 시각화 (5종 plot) |
| `submit_backtest_job.py` | Orchestration | 백테스트 job을 FileQueue에 제출 |
| `run_generation_worker.py` | Orchestration | 생성 job worker (polling daemon) |
| `run_backtest_worker.py` | Orchestration | 백테스트 job worker (polling daemon) |

## Shell 런처

| 스크립트 | 용도 |
|---------|------|
| `run_generate_review_backtest.sh` | generate → review → backtest 일괄 실행 (queue 미사용) |
| `run_local_stack.sh` | generation + backtest worker 동시 기동 (dev profile) |
| `run_generation_worker.sh` | generation worker 단순 래퍼 |
| `run_backtest_worker.sh` | backtest worker 단순 래퍼 |
| `submit_generation_job.sh` | generate_strategy.py 래퍼 |
| `submit_backtest_job.sh` | submit_backtest_job.py 래퍼 |
| `run_validation_tiers.sh` | smoke/stronger validation tier 실행 |

## 주의사항

- 모든 Python 스크립트는 `PYTHONPATH=src`가 필요함
- 모든 스크립트는 `--profile`과 `--config`로 설정 override 가능
- `collect_data.py`는 KIS API 자격증명(`~/KIS/config/kis_devlp.yaml`) 필요
- Worker는 `--once` 플래그로 단일 job 처리 후 종료 가능

## 관련 문서

- [../conf/README.md](../conf/README.md) — 설정 스택 및 프로필
- [../docs/COMMANDS.md](../docs/COMMANDS.md) — CLI 명령 치트시트
