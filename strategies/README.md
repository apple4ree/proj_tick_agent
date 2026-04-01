# strategies/

전략 스펙 파일 저장소.

`run_strategy_loop.py`가 생성한 전략 스펙은 `outputs/memory/strategies/{run_id}.json`에 저장된다.
이 디렉토리는 수동으로 보관/참조하고 싶은 스펙을 관리하는 용도로 사용한다.

## `examples/`

참고용 정적 샘플. 직접 실행 가능하다.

```bash
PYTHONPATH=src python scripts/backtest.py \
    --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
    --symbol 005930 --start-date 20260313
```

주의: `examples/`의 파일은 구 `StrategySpecV2` 포맷이다.
`run_strategy_loop.py`가 생성하는 Simple JSON Spec과 다른 포맷이므로 직접 루프에 주입하지 않는다.
