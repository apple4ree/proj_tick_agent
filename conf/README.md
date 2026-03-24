# conf/ — 설정 파일

YAML 기반 설정 스택과 프로필 시스템을 관리한다. `src/utils/config.py`의 `load_config()`가 이 디렉토리의 파일을 계층적으로 병합하여 최종 설정을 생성한다.

## 핵심 역할

- 앱 메타데이터, 경로, 생성/백테스트/워커 설정을 YAML로 선언
- 프로필(`profiles/`)로 환경별(dev/prod/smoke) override 제공
- 실험 재현을 위한 baseline 설정 템플릿 보관
- `EXPERIMENT_PROTOCOL.md`로 실험 규칙 문서화

## 기본 Config Stack (자동 병합 순서)

`load_config()`가 아래 순서로 deep-merge한다:

1. `app.yaml` — 앱 이름, env, log_level, timezone
2. `paths.yaml` — data_dir(H0STASP0), registry_dir, jobs_dir, outputs_dir
3. `generation.yaml` — spec_format(v2), backend(template|openai), mode, n_ideas
4. `backtest_base.yaml` — initial_cash, seed, fee_model, impact_model, slicing, placement
5. `backtest_worker.yaml` — latencies_ms sweep, review_gate_required
6. `workers.yaml` — poll_interval, once flag

이후 `--profile`로 지정한 프로필 YAML이 병합되고, `--config`로 지정한 명시적 override가 최종 적용된다.

## 자동 반영되지 않는 파일

| 파일 | 용도 | 비고 |
|------|------|------|
| `backtest_core.yaml` | qlib-style 상세 설정 템플릿 | PipelineRunner 직접 로드 시 사용. 기본 스택에 포함 안 됨 |
| `baseline.yaml` | 실험 baseline 설정 (005930, KRX fee, linear impact) | 참조용 |
| `baseline_mini.yaml` | 빠른 반복용 축소 baseline (10s resample, no attribution) | 참조용 |
| `env_config.yaml` | RL 환경 설정 (legacy, 현재 미사용) | 과거 LOB simulator용 |
| `train_config.yaml` | RL 학습 설정 (legacy, 현재 미사용) | 과거 PPO 학습용 |

## 프로필 사용법

```bash
# dev 프로필: template backend, mock mode, auto_approve
PYTHONPATH=src python scripts/backtest.py --spec ... --profile dev

# smoke 프로필: 빠른 wiring check
PYTHONPATH=src python scripts/backtest.py --spec ... --profile smoke

# prod 프로필: openai backend, live mode, strict review
PYTHONPATH=src python scripts/backtest.py --spec ... --profile prod
```

| 프로필 | env | backend | mode | 특징 |
|--------|-----|---------|------|------|
| `dev` | dev | template | mock | auto_approve, DEBUG log |
| `smoke` | dev | template | mock | latencies [0, 100], once: true |
| `prod` | prod | openai | live | WARNING log, static_review_required |

## 주의사항

- `paths.yaml`의 `data_dir`은 실제 H0STASP0 데이터 위치를 가리켜야 함
- `${VAR}` 및 `${VAR:-default}` 환경변수 확장 지원
- 상대 경로는 자동으로 절대 경로로 변환됨
- `env_config.yaml`, `train_config.yaml`은 RL 시절 잔재이며 현재 파이프라인에서 사용하지 않음

## 관련 문서

- [EXPERIMENT_PROTOCOL.md](EXPERIMENT_PROTOCOL.md) — 실험 수행 규칙(config merge, naming, validation policy)
- [../README.md](../README.md) — 프로젝트 개요
- [../docs/COMMANDS.md](../docs/COMMANDS.md) — CLI 명령 치트시트
