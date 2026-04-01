# scripts/

공개 CLI 진입점.

## Public CLI

| 스크립트 | 용도 | 주요 옵션 |
|---------|------|----------|
| `run_strategy_loop.py` | LLM 반복 전략 탐색 루프 | `--research-goal`, `--symbol`, `--start-date`, `--end-date`, `--mode`, `--model`, `--n-iter`, `--memory-dir`, `--output-dir`, `--config`, `--profile` |
| `backtest.py` | 단일 종목 백테스트 | `--spec`, `--symbol`, `--start-date`, `--end-date`, `--config`, `--profile` |

모든 Python 스크립트는 `PYTHONPATH=src` 환경에서 실행한다.

## run_strategy_loop.py

LLM 전략 생성 → Hard Gate → 백테스트 → 피드백 → 메모리 저장 루프를 실행한다.

```bash
# mock 모드 (LLM 없이 테스트)
PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "order imbalance momentum" \
    --symbol 005930 --start-date 20260313 \
    --mode mock --n-iter 3

# live 모드 (OpenAI API 필요)
OPENAI_API_KEY=sk-... PYTHONPATH=src python scripts/run_strategy_loop.py \
    --research-goal "spread mean reversion" \
    --symbol 005930 --start-date 20260313 --end-date 20260314 \
    --mode live --model gpt-4o --n-iter 10
```

옵션:

| 옵션 | 기본값 | 설명 |
|------|--------|------|
| `--research-goal` | (필수) | 자연어 전략 목표 |
| `--symbol` | (필수) | KRX 종목 코드 (예: 005930) |
| `--start-date` | (필수) | YYYYMMDD |
| `--end-date` | start-date와 동일 | YYYYMMDD |
| `--mode` | `mock` | `live` 또는 `mock` |
| `--model` | `gpt-4o-mini` | OpenAI 모델명 (live 모드) |
| `--n-iter` | `5` | 최대 이터레이션 수 |
| `--memory-dir` | `outputs/memory` | 메모리 저장 경로 |
| `--output-dir` | `outputs/backtests` | 백테스트 산출물 경로 |
| `--config` | — | YAML config 오버라이드 경로 |
| `--profile` | — | 설정 프로필 (dev, smoke, prod) |

출력 요약:
```
─── Loop Summary ────────────────────────────────────────────────
  Final verdict : retry
  Best run_id   : none
  Iterations    : 3
    [ 1] abc123  iter    retry
    [ 2] def456  iter    retry
    [ 3] ghi789  iter    retry
```

## backtest.py

단일 종목에 대해 spec JSON을 실행하고 산출물을 저장한다.

```bash
PYTHONPATH=src python scripts/backtest.py \
    --spec outputs/memory/strategies/abc123.json \
    --symbol 005930 --start-date 20260313 --end-date 20260314
```

`--spec`에 `run_strategy_loop`이 저장한 `strategies/{run_id}.json`을 직접 전달할 수 있다.
spec 내부의 `spec` 키에서 전략 정의를 추출한다.
