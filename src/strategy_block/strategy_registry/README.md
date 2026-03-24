# strategy_registry/ — 전략 Spec 저장소

StrategySpecV2와 메타데이터를 파일 시스템에 저장하고 상태(lifecycle)를 관리한다.

## 핵심 역할

- `{name}_v{version}.json` + `.meta.json` 쌍으로 spec/metadata 저장
- 상태 전이 관리: DRAFT → REVIEWED → APPROVED → PROMOTED_TO_BACKTEST → ...
- 실행 게이트 체크: static review 통과 + 적절한 상태 확인 후 spec 로드 허용
- 버전 조회, 최신 승인 버전 탐색

## 대표 파일

| 파일 | 역할 |
|------|------|
| `registry.py` | `StrategyRegistry` — 파일 기반 CRUD, 상태 전이, execution gate |
| `models.py` | `StrategyMetadata`, `StrategyStatus` 열거형, 유효 전이 규칙 |

## 상태 흐름

```
DRAFT → REVIEWED → APPROVED → PROMOTED_TO_BACKTEST → PROMOTED_TO_LIVE → ARCHIVED
```

`VALID_TRANSITIONS` DAG가 허용된 전이만 정의한다.

## 주요 API

- `save_spec(spec, metadata)` — spec + metadata 저장
- `load_spec(name, version)` — spec JSON 로드
- `get_metadata(name, version)` — metadata 조회
- `update_status(name, version, new_status)` — 상태 전이
- `check_execution_gate(name, version)` — static review + 상태 확인
- `load_spec_for_execution(name, version)` — gate 체크 + 컴파일
- `latest_approved(name)` — 최신 승인 버전 조회

## 전체 파이프라인에서의 위치

Generation이 spec을 여기에 저장하고, Worker/CLI가 여기서 spec을 로드하여 백테스트를 실행한다. 실제 저장 경로는 `strategies/` 디렉토리.

## 주의사항

- 파일 직접 편집 시 metadata와 불일치 가능
- `conf/paths.yaml`의 `registry_dir`이 저장 경로를 결정
- Worker는 `check_execution_gate()`를 호출하여 승인되지 않은 spec 실행을 방지

## 관련 문서

- [../../../../strategies/README.md](../../../../strategies/README.md) — 실제 저장소 디렉토리
- [../strategy_review/README.md](../strategy_review/README.md) — review 결과가 metadata에 반영
