# strategies/examples/

참고용 정적 전략 샘플.

이 디렉토리의 파일들은 구 `StrategySpecV2` AST 포맷이다.
`run_strategy_loop.py`가 생성하는 Simple JSON Spec과 다른 포맷이므로 루프에 직접 주입하지 않는다.

`backtest.py`로 직접 실행 가능:

```bash
PYTHONPATH=src python scripts/backtest.py \
    --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
    --symbol 005930 --start-date 20260313
```
