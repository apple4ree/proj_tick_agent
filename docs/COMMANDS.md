# Commands (v2-only)

자주 쓰는 운영 명령만 모은 치트시트다. 모든 경로는 StrategySpec v2 기준이다.

## 1) Generate

```bash
cd /home/dgu/tick/proj_rl_agent
PYTHONPATH=src python scripts/generate_strategy.py \
  --goal "order imbalance alpha" \
  --direct \
  --backend template \
  --mode mock
```

- 생성 결과 경로는 `GENERATED_SPEC=<path>`로 출력된다.

## 2) Review

```bash
PYTHONPATH=src python scripts/review_strategy.py \
  strategies/examples/stateful_cooldown_momentum_v2.0.json
```

## 3) Single-Symbol Backtest

```bash
PYTHONPATH=src python scripts/backtest.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --symbol 005930 \
  --start-date 20260313 \
  --profile smoke
```

## 4) Universe Backtest

```bash
PYTHONPATH=src python scripts/backtest_strategy_universe.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --start-date 20260313 \
  --profile dev
```

## 5) Workers

Generation worker:

```bash
PYTHONPATH=src python scripts/run_generation_worker.py --once
```

Backtest worker:

```bash
PYTHONPATH=src python scripts/run_backtest_worker.py --once
```

## 6) End-to-End Launcher

```bash
bash scripts/run_generate_review_backtest.sh \
  --goal "microstructure momentum" \
  --symbol 005930 \
  --start-date 20260313 \
  --profile smoke
```

## 7) Validation Tiers

Quick wiring check (smoke):

```bash
./scripts/run_validation_tiers.sh smoke
```

Stronger integration/regression:

```bash
./scripts/run_validation_tiers.sh stronger
```

## 8) Examples and Outputs

Examples 목록:

```bash
ls -1 strategies/examples
```

최근 산출물 확인:

```bash
find outputs -maxdepth 2 -mindepth 1 -type d | sort
```

## 9) CLI Help

```bash
PYTHONPATH=src python scripts/generate_strategy.py --help
PYTHONPATH=src python scripts/review_strategy.py --help
PYTHONPATH=src python scripts/backtest.py --help
PYTHONPATH=src python scripts/backtest_strategy_universe.py --help
PYTHONPATH=src python scripts/run_generation_worker.py --help
PYTHONPATH=src python scripts/run_backtest_worker.py --help
```
