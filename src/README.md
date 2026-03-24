# src/ — 실행 로직

프로젝트의 모든 Python 소스 코드가 위치한다. 5개 블록 + utils로 구성되며, 7-Layer 파이프라인을 구현한다.

## 핵심 역할

- Layer 0~7 백테스트 파이프라인의 전체 구현
- 전략 spec 생성/검토/컴파일/실행의 v2-only 경로
- 설정 로더, 로깅 등 공통 유틸리티

## 하위 디렉토리

| 디렉토리 | Block | Layer | 역할 |
|----------|-------|-------|------|
| `data/` | Data | 0 | 원시 데이터 적재, 정제, 동기화, MarketState 생성 |
| `strategy_block/` | Strategy | — | v2 spec 생성/검토/저장/컴파일 |
| `execution_planning/` | Exec Planning | 1~4 | signal → target → order → execution planning |
| `market_simulation/` | Market Sim | 5 | fill/latency/impact/fee 시뮬레이션 |
| `evaluation_orchestration/` | Eval & Orch | 6~7 | 백테스트 실행, 메트릭, worker orchestration |
| `utils/` | — | — | config 로더, 로깅, 메트릭 유틸리티 |

## 파이프라인 데이터 흐름

```
MarketState → Signal → TargetPosition → ParentOrder → ChildOrder → FillEvent → Reports
  (data)      (L1)       (L2)             (L3)          (L4)        (L5)       (L6-7)
```

## 전체 파이프라인에서의 위치

`src/`는 코드 본체다. `scripts/`가 여기의 모듈을 import하여 CLI를 제공하고, `conf/`가 설정을 공급하며, `strategies/`가 spec 저장소 역할을 한다.

## 주의사항

- 스크립트에서 import 시 `PYTHONPATH=src` 필요
- Layer 간 의존성은 단방향 (하위 → 상위 방향으로만 흐름)
- v1 관련 코드는 제거됨. 현재 v2-only

## 관련 문서

- [../ARCHITECTURE.md](../ARCHITECTURE.md) — 5-Block 아키텍처
- [../PIPELINE.md](../PIPELINE.md) — Layer 0~7 상세
