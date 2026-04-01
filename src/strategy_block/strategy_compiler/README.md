# strategy_compiler/

현재 이 패키지에서 실제로 사용되는 것은 `v2/features.py` 하나뿐이다.

## v2/features.py

`BUILTIN_FEATURES`: `strategy_loop`의 JSON 스펙에서 `feature` 키로 참조 가능한 피처 이름 목록.

`HardGate`와 `PromptBuilder`가 이 목록을 참조한다.
- `HardGate`: 스펙 내 feature 이름이 목록에 존재하는지 검증
- `PromptBuilder`: LLM 프롬프트에 사용 가능한 피처 목록을 주입

`extract_builtin_features(state: MarketState) → dict[str, float]`: MarketState에서 피처 값을 추출.
`SimpleSpecStrategy`가 매 틱마다 호출한다.
