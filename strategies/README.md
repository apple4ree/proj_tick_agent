# strategies/ — Runtime Strategy Registry

전략 spec의 **운영 저장소(runtime registry)**다. `StrategyRegistry`가 이 디렉토리에 spec과 metadata를 저장하고 관리한다.

## 핵심 역할

- v2 전략 spec JSON의 버전 관리된 저장소
- 생성/검토/승인/백테스트 상태 추적 (metadata)
- Worker와 CLI가 실행 시 여기서 spec을 로드
- 현재 **v2-only** — v1 spec은 사용하지 않음

## 파일 구조

```
strategies/
├── {name}_v{version}.json        # StrategySpecV2 JSON
├── {name}_v{version}.meta.json   # 메타데이터 (상태, 생성일, 리뷰 결과 등)
└── examples/                     # 참고용 정적 샘플 (registry 아님)
```

## examples/ vs registry (이 디렉토리) 차이

| 항목 | `strategies/` (registry) | `strategies/examples/` |
|------|--------------------------|------------------------|
| 역할 | 운영 저장소 | 참고용 샘플 |
| 버전 추적 | `.meta.json`으로 상태 관리 | 없음 |
| 생성 경로 | `generate_strategy.py` / worker | 수동 배치 |
| 백테스트 대상 | O (execution gate 통과 필요) | △ (직접 `--spec` 지정 시만) |

## Metadata 상태 흐름

```
DRAFT → REVIEWED → APPROVED → PROMOTED_TO_BACKTEST → PROMOTED_TO_LIVE → ARCHIVED
```

`check_execution_gate()`는 static review 통과 + 적절한 상태를 확인한 후에만 spec 로드를 허용한다.

## 주의사항

- 이 디렉토리를 직접 수정하면 metadata와 불일치가 발생할 수 있음
- Spec 추가/수정은 `generate_strategy.py` 또는 `StrategyRegistry` API를 통해 수행
- 현재 등록된 spec은 모두 v2 format

## 관련 문서

- [examples/README.md](examples/README.md) — 참고용 v2 샘플 설명
- [../src/strategy_block/strategy_registry/README.md](../src/strategy_block/strategy_registry/README.md) — Registry 구현
- [../README.md](../README.md) — 프로젝트 개요
