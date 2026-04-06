# strategies/

코드 전략 파일 저장소.

`backtest.py`는 Python 코드 파일(`--code-file`)을 직접 실행한다.
생성 루프 산출물(`outputs/memory*/strategies/{run_id}.json`)에는 코드 문자열이 포함된다.

## 사용 예시

```bash
PYTHONPATH=src python scripts/backtest.py \
    --code-file path/to/strategy.py \
    --symbol 005930 --start-date 20260313
```
