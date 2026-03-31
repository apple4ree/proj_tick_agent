# Commands

## Core Commands

### 1. Generate

```bash
# OpenAI backend (generation mode/live 여부는 config/profile에서 resolve)
OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/generate_strategy.py \
  --goal "order imbalance alpha" --direct --backend openai

# Template backend (API 키 불필요)
PYTHONPATH=src python scripts/generate_strategy.py \
  --goal "order imbalance alpha" --direct --backend template
```

`--backend openai`이고 resolved generation mode가 `live`면 `OPENAI_API_KEY`가 필요하다.

### 2. Review

```bash
PYTHONPATH=src python scripts/review_strategy.py \
  strategies/examples/stateful_cooldown_momentum_v2.0.json --mode auto-repair
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

bash scripts/run_generate_review_backtest.sh \
  --goal "microstructure momentum" --symbol 005930 --start-date 20260313 \
  --review-mode auto-repair

OPENAI_API_KEY=sk-... bash scripts/run_generate_review_backtest.sh \
  --goal "microstructure momentum" --symbol 005930 --start-date 20260313 \
  --backend openai --review-mode auto-repair
```

`run_generate_review_backtest.sh`는 generation/review/backtest stdout+stderr를 실시간으로 출력하고,
동시에 `/tmp/proj_gen_e2e.log`, `/tmp/proj_review_e2e.log`, `/tmp/proj_backtest_e2e.log`에 저장한다.
`--review-mode auto-repair`에서 `repaired_spec.json`이 생성되면 해당 spec가 backtest에 사용될 수 있다.

### 6. Universe Batch Wrapper

```bash
# smoke preset (template/dev sanity-check)
bash scripts/run_generate_review_backtest_batch.sh \
  --goals-file conf/goals/universe_goals_smoke.txt \
  --start-date 2026-03-13 \
  --end-date 2026-03-13 \
  --profile smoke \
  --backend template \
  --review-mode static

# openai preset (production-like constraints)
OPENAI_API_KEY=sk-... bash scripts/run_generate_review_backtest_batch.sh \
  --goals-file conf/goals/universe_goals_openai.txt \
  --start-date 2026-03-13 \
  --end-date 2026-03-13 \
  --profile prod \
  --backend openai \
  --review-mode auto-repair
```

batch goals 파일은 한 줄당 goal 1개를 사용하며, 빈 줄/`#` 주석 줄은 무시된다.

### CLI Help

```bash
PYTHONPATH=src python scripts/generate_strategy.py --help
PYTHONPATH=src python scripts/review_strategy.py --help
PYTHONPATH=src python scripts/backtest.py --help
PYTHONPATH=src python scripts/backtest_strategy_universe.py --help
bash scripts/run_generate_review_backtest.sh --help
bash scripts/run_generate_review_backtest_batch.sh --help
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
