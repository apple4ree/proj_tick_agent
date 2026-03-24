# Experiment Protocol (v2-only)

이 문서는 StrategySpec v2 실험 실행 규약이다. 목적은 결과 재현성과 산출물 관리를 일관되게 유지하는 것이다.

## 1) Scope

- 대상: generation -> review -> backtest (single/universe) -> report
- 기본 spec 형식: StrategySpec v2
- smoke는 배선 확인용이며 최종 품질 판단은 stronger validation에서 수행

## 2) Config/Profile Rules

기본 설정 병합 순서:

1. `conf/app.yaml`
2. `conf/paths.yaml`
3. `conf/generation.yaml`
4. `conf/backtest_base.yaml`
5. `conf/backtest_worker.yaml`
6. `conf/workers.yaml`
7. `conf/profiles/<profile>.yaml`
8. `--config <override.yaml>`

운영 규칙:

- 빠른 점검: `--profile smoke`
- 일반 개발 검증: `--profile dev`
- 긴 회귀/운영 유사 점검: `--profile prod` 또는 명시 override

## 3) Naming and Tracking

실험 실행 시 다음을 기록한다.

- goal 텍스트
- spec 경로 (example/generated/registry 구분 포함)
- symbol/date/profile/config override
- backend/mode (template/openai, live/mock/replay)
- 실행 시각(UTC)과 git revision

권장 실행 단위:

- 하나의 목표(goal)당 하나의 명시적 run 메모
- run 메모에는 command line과 출력 경로를 함께 기록

## 4) Artifact Management

- `outputs/`: 백테스트/요약 등 실행 산출물
- `experiments/`: 반복 실험 결과 묶음
- `checkpoints/`: 학습 산출물

원칙:

- 코드와 산출물은 분리 관리
- 큰 산출물은 주기적으로 정리
- 재현에 필요 없는 임시 산출물은 보관하지 않음

## 5) Spec Source Separation

- `strategies/examples/`: reference-only 샘플
- 생성 직후 spec: generate 결과 파일 (`GENERATED_SPEC`)
- runtime registry spec: `strategies/` 아래 승인/버전 관리 대상

실험 시 spec 출처를 반드시 태깅한다.

- `source=example`
- `source=generated`
- `source=registry`

## 6) Validation Policy

smoke (quick wiring check):

- CLI/help 경로 정상 여부
- generate/review/backtest 최소 경로 동작
- 짧은 입력으로 빠른 실행

stronger validation:

- fill/latency/impact가 실제 발생하는 통합 경로
- profile/config 조합 회귀
- worker 경로 포함 점검

판정 규칙:

- smoke pass만으로 품질 승인하지 않음
- stronger pass를 릴리즈/보고 기준으로 사용

## 7) Reproducibility Minimum

최소 재현 정보:

- spec 파일 원본
- 실행 명령 전체
- profile + override config
- 데이터 기간/심볼 범위
- 출력 결과 경로

동일 조건 재실행 시 결과 차이가 크면:

1. config diff 확인
2. spec diff 확인
3. 데이터 구간/입력 파일 차이 확인
4. 코드 revision 차이 확인

## 8) Recommended Command Patterns

단일 종목 smoke:

```bash
PYTHONPATH=src python scripts/backtest.py \
  --spec strategies/examples/stateful_cooldown_momentum_v2.0.json \
  --symbol 005930 --start-date 20260313 --profile smoke
```

통합 stronger:

```bash
./scripts/run_validation_tiers.sh stronger
```

end-to-end:

```bash
bash scripts/run_generate_review_backtest.sh \
  --goal "microstructure momentum" \
  --symbol 005930 \
  --start-date 20260313 \
  --profile dev
```
