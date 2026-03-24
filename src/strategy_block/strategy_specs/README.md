# strategy_specs/ — v2 전략 스키마 및 AST

StrategySpecV2의 데이터 모델과 AST(Abstract Syntax Tree) 노드를 정의한다. 전략의 "언어"에 해당하는 IR(Intermediate Representation)이다.

## 핵심 역할

- StrategySpecV2 전체 스키마 정의 (preconditions, entry/exit/risk/execution/state policy)
- 조건식 AST 노드 체계 (12종 노드 타입)
- JSON ↔ Python 직렬화/역직렬화
- Phase 1~3 기능의 스키마 수준 지원

## 대표 파일 (`v2/`)

| 파일 | 역할 |
|------|------|
| `schema_v2.py` | `StrategySpecV2`, `EntryPolicyV2`, `ExitPolicyV2`, `RiskPolicyV2`, `ExecutionPolicyV2`, `RegimeV2`, `StatePolicyV2` 정의 |
| `ast_nodes.py` | `ExprNode` 추상 베이스 + 12종 구체 노드, `expr_from_dict()` 역직렬화 |

## AST 노드 체계

| 범주 | 노드 | 용도 |
|------|------|------|
| Value | `ConstExpr`, `FeatureExpr`, `StateVarExpr`, `PositionAttrExpr` | 상수, 피처, 상태변수, 포지션 속성 |
| Condition | `ComparisonExpr`, `AllExpr`, `AnyExpr`, `NotExpr`, `CrossExpr` | 조건 결합 및 비교 |
| Phase 2 | `LagExpr`, `RollingExpr`, `PersistExpr` | 시간 지연, 윈도우 통계, 지속 조건 |

## StrategySpecV2 구조

```
StrategySpecV2
├── preconditions: [ExprNode]       # 시장 수준 진입 게이트
├── entry_policies: [EntryPolicyV2] # 진입 정책 (side, trigger, strength, cooldown)
├── exit_policies: [ExitPolicyV2]   # 퇴출 정책 (priority 기반 규칙)
├── risk_policy: RiskPolicyV2       # 포지션 크기, degradation 규칙
├── execution_policy: ExecutionPolicyV2  # 배치 모드, 취소/repricing
├── regimes: [RegimeV2]             # Phase 2: 시장 regime 라우팅
└── state_policy: StatePolicyV2     # Phase 3: 런타임 상태변수, guard, event
```

## 전체 파이프라인에서의 위치

Generation이 템플릿에서 이 스키마의 인스턴스를 생성하고, Reviewer가 정적 검증하며, Compiler가 이를 읽어 실행 가능한 Strategy로 변환한다.

## 주의사항

- 모든 노드는 `to_dict()` / `expr_from_dict()`로 JSON 왕복 가능
- Phase 3 노드(StateVarExpr, PositionAttrExpr)는 런타임 상태에 의존
- 새 노드 타입 추가 시 `expr_from_dict()`와 `compiler_v2.py`의 evaluate 함수 양쪽을 업데이트해야 함

## 관련 문서

- [../strategy_compiler/README.md](../strategy_compiler/README.md) — AST 노드 evaluate 구현
- [../strategy_generation/README.md](../strategy_generation/README.md) — 템플릿에서 spec 생성
