# orchestration/ — Job Worker 및 큐 관리

FileQueue 기반 비동기 job 처리 시스템이다. 전략 생성과 백테스트를 worker 패턴으로 실행한다.

## 핵심 역할

- 파일 기반 job queue: `os.rename()` 원자적 전이로 race-safe
- GenerationWorker: 생성 job 처리 → spec 생성 → registry 저장
- BacktestWorker: 백테스트 job 처리 → 단일/universe 백테스트 실행
- OrchestrationManager: job 제출/조회 편의 레이어

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `models.py` | `Job`, `JobType`, `JobStatus` | Job 데이터 모델, 상태 전이 규칙 |
| `file_queue.py` | `FileQueue` | 파일 기반 큐 (atomic rename, 디렉토리별 상태 분리) |
| `generation_worker.py` | `GenerationWorker` | 생성 job dequeue → generate → registry save |
| `backtest_worker.py` | `BacktestWorker` | 백테스트 job dequeue → compile → run → save |
| `manager.py` | `OrchestrationManager` | 제출/조회 편의 API |

## FileQueue 디렉토리 레이아웃

```
jobs/
├── queued/      # 대기 중 job JSON
├── running/     # 실행 중 (atomic rename으로 전이)
├── succeeded/   # 성공 완료
├── failed/      # 실패
└── cancelled/   # 취소
```

`dequeue()`는 `queued/ → running/` 원자적 rename. 동시에 여러 worker가 돌아도 하나의 job은 정확히 하나의 worker만 처리.

## Direct CLI vs Worker 경로

| 경로 | 사용 도구 | 특징 |
|------|----------|------|
| Direct CLI | `backtest.py`, `generate_strategy.py --direct` | 즉시 실행, 소규모 실험 |
| Worker | `run_*_worker.py` + `submit_*_job.sh` | Polling daemon, 대규모/자동화 |

Worker는 `--once` 플래그로 단일 job만 처리 후 종료 가능. `run_local_stack.sh`로 양쪽 worker 동시 기동.

## Job 상태 전이

```
QUEUED → RUNNING → SUCCEEDED
                 → FAILED
       → CANCELLED
```

## BacktestWorker 실행 흐름

1. Job dequeue (SINGLE_BACKTEST 또는 UNIVERSE_BACKTEST)
2. Registry에서 spec 로드 (execution gate 체크)
3. Spec 컴파일
4. 단일: 데이터 로드 → PipelineRunner 실행 → summary 저장
5. Universe: 전종목 × latency sweep → universe_results.json 저장

## 주의사항

- FileQueue 사용. Redis/RabbitMQ 등 분산 큐가 아님
- 원자적 rename(`os.rename()`)이 동시성 보장의 유일한 메커니즘
- Job JSON에 결과 경로(`result_path`) 또는 에러 메시지(`error_message`) 기록
- Worker 설정은 `conf/workers.yaml`에서 관리 (poll_interval, once flag)

## 관련 문서

- [../layer7_validation/README.md](../layer7_validation/README.md) — PipelineRunner (worker가 호출)
- [../../../../scripts/README.md](../../../../scripts/README.md) — Worker 런처 스크립트
- [../../../../conf/README.md](../../../../conf/README.md) — workers.yaml 설정
