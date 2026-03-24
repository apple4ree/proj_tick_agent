# Strategy Examples (v2)

`strategies/examples/`는 StrategySpec v2 참고용 샘플 모음이다. 이 디렉토리는 runtime registry가 아니다.

## Role

- reference-only 샘플 제공
- generation/review/backtest 명령 예제의 입력 spec 제공
- 운영 승인/버전 추적은 `strategies/` registry 경로에서 수행

## Current Canonical Examples

- `stateful_cooldown_momentum_v2.0.json`
- `position_aware_time_exit_momentum_v2.0.json`
- `regime_filtered_persist_momentum_v2.0.json`

## Typical Flow

1. examples에서 시작해 spec 구조를 확인
2. `scripts/generate_strategy.py`로 신규 v2 spec 생성
3. `scripts/review_strategy.py`로 정적 검토
4. `scripts/backtest.py` 또는 worker 경로로 실행
5. 승인/보관이 필요하면 registry(`strategies/`)에 반영
