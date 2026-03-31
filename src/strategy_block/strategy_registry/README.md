# strategy_registry/ — Spec 저장 + Trial/Family 후보 인덱스

`strategy_registry`는 세 계층을 함께 제공한다.

1. **Spec Registry (`registry.py`)**
- `StrategySpecV2` + metadata 저장/상태전이
- execution gate 체크

2. **Candidate Tracking Foundation (PR1~PR3)**
- `trial_registry.py`: trial record/stage/status 저장
- `lineage.py`: parent-child lineage graph
- `family_fingerprint.py`: deterministic coarse family fingerprint
- `family_index.py`: family member 관리 + duplicate/neighbor 탐색

3. **Selection Discipline Foundation (PR5)**
- `trial_accounting.py`: registry snapshot 기반 trial/family/stage/reject 집계
- walk-forward selector가 읽을 family crowding / duplicate search pressure foundation
- selection snapshot artifact의 audit input foundation

## 핵심 역할

- `{name}_v{version}.json` + `.meta.json` spec lifecycle 관리
- trial 단위 후보 추적(`DRAFT/REVIEWED/APPROVED/BACKTESTED/WF_PASSED/PROMOTION_CANDIDATE/CONTRACT_EXPORTED/HANDOFF_READY`)
- family id 기반 후보군 압축을 위한 deterministic coarse grouping
- duplicate/neighbor lookup + trial accounting 기반 selection discipline foundation 제공

## Family Fingerprint (PR3)

`FamilyFingerprintBuilder`는 spec에서 아래 축을 추출한다.

- `motif`
- `side_model`
- `execution_style`
- `horizon_bucket`
- `regime_shape`
- `feature_signature`

`family_id`는 spec 이름/버전 문자열이 아니라 coarse structural signature 해시를 사용한다.

## Family Index (PR3)

`FamilyIndex`는 file-based JSON 저장을 사용한다.

- 기본 저장 경로: `outputs/trials/family_index`
- `upsert()`: family member 병합
- `list_members()`: family 구성원 조회
- `find_duplicate_or_neighbor()`: similarity 기반 duplicate/neighbor 후보 탐색

`strategies/`를 operational family index 저장소로 사용하지 않는다.

## Trial Registry / Accounting (PR5)

`TrialRecord.family_id`는 first-class field이며,
`TrialRegistry.attach_family()`로 trial에 family를 연결할 수 있다.

`TrialRegistry.list_all()` / `list_active()`는 selector/report 계층이 registry snapshot을 deterministic하게 읽을 수 있게 한다.

`TrialAccounting`은 아래 집계를 제공한다.

- total / active / rejected trial count
- family별 total / active trial count
- stage count
- reject reason count

이 계층은 DB나 별도 통계 인프라 없이 file-based registry snapshot만으로 동작한다.

## 현재 의미

PR5 이후 walk-forward selection은 family-aware trial accounting을 **soft penalty + decision trace**로 사용한다.

목적은 더 많은 전략을 고르는 것이 아니라,
같은 family를 과도하게 반복 시도해서 생기는 selection 착시를 조금 더 일찍 불리하게 만드는 것이다.

`selection_snapshot.json`은 promotion gate 이전 단계의 decision trace artifact다.

## 아직 범위 밖 (Deferred)

- formal multiple testing correction
- novelty-guided generation coupling
- live/paper/shadow trading 연결
- DB-backed registry / statistical warehouse

## 관련 문서

- `../strategy_generation/README.md`
- `../strategy_review/README.md`
- `../../../../PIPELINE.md`
