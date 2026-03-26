# proj_rl_agent

틱 데이터 기반 전략 생성/검토/백테스트 워크스페이스.

## Quick Start

```bash
cd /home/dgu/tick/proj_rl_agent

# 1. 전략 생성 (OpenAI live)
OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/generate_strategy.py \
  --goal "order imbalance alpha" --direct --backend openai --mode live

# 2. 전략 검토
PYTHONPATH=src python scripts/review_strategy.py \
  strategies/examples/stateful_cooldown_momentum_v2.0.json

# 3. 단일 종목 백테스트
PYTHONPATH=src python scripts/backtest.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --symbol 005930 --start-date 20260313

# 4. Universe 백테스트
PYTHONPATH=src python scripts/backtest_strategy_universe.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --start-date 20260313

# 5. End-to-end (생성 → 검토 → 백테스트)
bash scripts/run_generate_review_backtest.sh \
  --goal "microstructure momentum" --symbol 005930 --start-date 20260313
```

## 핵심 디렉토리

| 디렉토리 | 역할 |
|----------|------|
| `scripts/` | 공개 CLI (5개) |
| `scripts/internal/workers/` | 내부 worker 스크립트 |
| `scripts/internal/ops/` | 내부 운영 도구 (validation, job 제출) |
| `scripts/internal/adhoc/` | 내부 데이터 수집/시각화/집계 |
| `src/` | 소스 코드 |
| `conf/` | 핵심 설정 (6개 YAML) |
| `conf/advanced/` | 실험/레거시 설정 |
| `strategies/` | 전략 registry |
| `strategies/examples/` | 참고용 v2 샘플 |

## 공개 CLI

| 스크립트 | 용도 |
|---------|------|
| `scripts/generate_strategy.py` | 전략 생성 (openai/template backend) |
| `scripts/review_strategy.py` | 전략 정적 검토 |
| `scripts/backtest.py` | 단일 종목 백테스트 |
| `scripts/backtest_strategy_universe.py` | 전종목 × 다 latency 백테스트 |
| `scripts/run_generate_review_backtest.sh` | 생성 → 검토 → 백테스트 일괄 실행 |

## Generated Artifacts (git 미추적)

`outputs/`, `logs/`, `jobs/`, `experiments/`, `checkpoints/`는 런타임 산출물 디렉토리다.
git이 추적하지 않으며 (`.gitignore`), 코드가 필요 시 자동 생성한다.

## 현재 한계

- reviewer는 static/heuristic 규칙 기반 점검
- production OMS/live trading 엔진이 아님
