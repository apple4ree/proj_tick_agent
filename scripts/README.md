# scripts/

공개 CLI 진입점.

## Public CLI

| 스크립트 | 용도 | 주요 옵션 |
|---------|------|----------|
| `run_strategy_loop.py` | 코드 전략 반복 탐색 루프 | `--research-goal`, `--symbols/--symbol`, `--is-start`, `--is-end`, `--oos-start`, `--oos-end`, `--mode`, `--model`, `--n-iter`, `--memory-dir`, `--output-dir`, `--config`, `--profile` |
| `run_code_loop_live.sh` | live 코드 루프 실행 래퍼 | 환경변수 `OPENAI_API_KEY`, `GOAL`, `SYMBOLS`, `IS_*`, `OOS_*`, `MODEL`, `N_ITER` |
| `run_code_loop_smoke.sh` | mock smoke 래퍼 | 환경변수 `IS_*`, `SYMBOLS`, `N_ITER`, `OPTIMIZE_N_TRIALS` |
| `backtest.py` | 단일 코드 전략 백테스트 | `--code-file`, `--symbol`, `--start-date`, `--end-date`, `--config`, `--profile` |

모든 Python 스크립트는 `PYTHONPATH=src` 환경에서 실행한다.

## run_strategy_loop.py

코드 전략 생성 → Hard Gate → 분포 필터 → 백테스트 → 피드백 → 메모리 저장 루프를 실행한다.

```bash
# mock 모드 (LLM 없이 테스트)
PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "order imbalance momentum" \
    --symbol 005930 --is-start 20260313 --is-end 20260313 \
    --mode mock --n-iter 3

# live 모드 (OpenAI API 필요)
OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "spread mean reversion" \
    --symbol 005930 \
    --is-start 20260313 --is-end 20260314 \
    --mode live --model gpt-4o --n-iter 10
```

## backtest.py

단일 종목에 대해 Python 코드 전략 파일을 실행하고 산출물을 저장한다.

```bash
PYTHONPATH=src python scripts/backtest.py \
    --code-file path/to/strategy.py \
    --symbol 005930 --start-date 20260313 --end-date 20260314
```
