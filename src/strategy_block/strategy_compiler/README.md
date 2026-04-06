# strategy_compiler/

현재 이 패키지에서 실제로 사용되는 것은 `v2/features.py` 하나뿐이다.

## v2/features.py

`BUILTIN_FEATURES`: strategy_loop 코드 생성 프롬프트에 주입되는 사용 가능 피처 이름 목록.

`extract_builtin_features(state: MarketState) -> dict[str, float]`:
MarketState에서 피처 값을 추출하며, `CodeStrategy`와 distribution filter/optimizer가 사용한다.
