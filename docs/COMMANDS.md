# Commands

## Core Commands

### 1. Generate

```bash
# OpenAI live (권장)
OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/generate_strategy.py \
  --goal "order imbalance alpha" --direct --backend openai --mode live

# Template (API 키 불필요)
PYTHONPATH=src python scripts/generate_strategy.py \
  --goal "order imbalance alpha" --direct --backend template
```

### 2. Review

```bash
PYTHONPATH=src python scripts/review_strategy.py \
  strategies/examples/stateful_cooldown_momentum_v2.0.json
```

### 3. Single-Symbol Backtest

```bash
PYTHONPATH=src python scripts/backtest.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --symbol 005930 --start-date 20260313
```

### 4. Universe Backtest

```bash
PYTHONPATH=src python scripts/backtest_strategy_universe.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --start-date 20260313
```

### 5. End-to-End

```bash
bash scripts/run_generate_review_backtest.sh \
  --goal "microstructure momentum" --symbol 005930 --start-date 20260313
```

### CLI Help

```bash
PYTHONPATH=src python scripts/generate_strategy.py --help
PYTHONPATH=src python scripts/review_strategy.py --help
PYTHONPATH=src python scripts/backtest.py --help
PYTHONPATH=src python scripts/backtest_strategy_universe.py --help
```

## Internal Commands

### Workers (`scripts/internal/workers/`)

```bash
PYTHONPATH=src python scripts/internal/workers/run_generation_worker.py --once
PYTHONPATH=src python scripts/internal/workers/run_backtest_worker.py --once
./scripts/internal/workers/run_local_stack.sh  # 양쪽 worker 동시 기동
```

### Ops (`scripts/internal/ops/`)

```bash
./scripts/internal/ops/run_validation_tiers.sh smoke
./scripts/internal/ops/run_validation_tiers.sh stronger
./scripts/internal/ops/submit_backtest_job.sh --strategy ... --symbol 005930 --start-date ...
```

### Ad-hoc (`scripts/internal/adhoc/`)

```bash
python scripts/internal/adhoc/visualize.py --run-dir outputs/backtests/<run_id>
PYTHONPATH=src python scripts/internal/adhoc/summarize_universe_results.py --results ...
PYTHONPATH=. python scripts/internal/adhoc/collect_data.py 005930 000660
```
