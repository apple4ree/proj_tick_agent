# src/strategy_block/ — 전략 Spec 라이프사이클 (Block 2)

v2 전략 spec의 생성, 검토, 저장, 컴파일을 담당하는 블록이다. 선언적 JSON spec을 실행 가능한 Strategy 객체로 변환하는 전체 경로를 구현한다.

## 핵심 역할

- StrategySpecV2 스키마 정의 및 AST 노드 체계
- 템플릿 기반 전략 생성 (v2-only canonical path)
- 정적 규칙 기반 검토 (hard gate + warning)
- 파일 기반 registry에 spec+metadata 저장/상태 관리
- Spec → executable Strategy 컴파일 (interpreter-style)

## 하위 디렉토리

| 디렉토리 | 역할 |
|----------|------|
| `strategy_specs/` | StrategySpecV2 스키마, AST 노드 정의 |
| `strategy_generation/` | v2 템플릿 기반 전략 생성, lowering |
| `strategy_review/` | v2 정적 규칙 검토 (20개 체크) |
| `strategy_registry/` | spec+metadata 파일 기반 저장소, 상태 관리 |
| `strategy_compiler/` | v2 spec → executable Strategy 컴파일 |
| `strategy/` | Strategy ABC 인터페이스 (`generate_signal`, `reset`) |

## 전략 라이프사이클

```
Goal → Template Selection → Lowering → StrategySpecV2
  → Static Review (hard gate) → Registry Save
  → Compile → CompiledStrategyV2 → Backtest에서 실행
```

## 전체 파이프라인에서의 위치

Data(Block 1)이 MarketState를 생성하면, 이 블록에서 컴파일된 Strategy가 `generate_signal(state)`을 호출하여 Signal을 생성한다. Signal은 Execution Planning(Block 3)으로 전달된다.

## 현재 제한사항

- Generation: OpenAI backend는 template으로 자동 fallback됨 (v2에서 실질적 미사용)
- Reviewer: 정적/heuristic 규칙 기반. LLM 기반 리뷰는 제거됨
- v1 spec/코드는 완전 제거됨. v2-only

## 관련 문서

- [../../ARCHITECTURE.md](../../ARCHITECTURE.md) — StrategySpec v2 Model, Compiler/Reviewer/Registry 관계
- [../../PIPELINE.md](../../PIPELINE.md) — Block 2: Strategy 상세
