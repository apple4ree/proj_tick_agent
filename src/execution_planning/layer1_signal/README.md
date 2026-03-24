# layer1_signal/ — 시그널 데이터 계약 (Layer 1)

전략이 생성하는 알파 시그널의 데이터 타입을 정의한다.

## 핵심 역할

- `Signal` dataclass: 방향 예측과 메타데이터를 담는 단일 데이터 계약
- Layer 1(알파 생성)과 Layer 2(포지션 관리) 사이의 인터페이스

## 대표 파일

| 파일 | 역할 |
|------|------|
| `signal.py` | `Signal` dataclass — score, confidence, expected_return, tags |

## Signal 구조

| 필드 | 타입 | 의미 |
|------|------|------|
| `timestamp` | datetime | 시그널 생성 시각 |
| `symbol` | str | 종목 코드 |
| `score` | float [-1, +1] | 방향 예측 (-1 매도, +1 매수) |
| `expected_return` | float | 기대 수익률 (bps) |
| `confidence` | float [0, 1] | 신뢰도 |
| `horizon_steps` | int | 예측 horizon (틱 수) |
| `tags` | dict | 임의 메타데이터 (execution hint 등) |
| `is_valid` | bool | 품질 게이트 플래그 |

## 전체 파이프라인에서의 위치

```
CompiledStrategyV2.generate_signal(MarketState) → Signal → TargetBuilder (Layer 2)
```

## 주의사항

- `tags`에 execution hint(placement_mode, cancel_after_ticks 등)가 포함될 수 있음
- `is_valid=False`이면 downstream에서 무시됨

## 관련 문서

- [../layer2_position/README.md](../layer2_position/README.md) — Signal을 소비하여 TargetPosition 생성
