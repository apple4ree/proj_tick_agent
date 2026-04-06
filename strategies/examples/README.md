# strategies/examples/

참고용 코드 전략 샘플 디렉토리.

`backtest.py`는 Python 코드 파일을 `--code-file`로 받아 실행한다.

```bash
PYTHONPATH=src python scripts/backtest.py \
    --code-file path/to/strategy.py \
    --symbol 005930 --start-date 20260313
```
