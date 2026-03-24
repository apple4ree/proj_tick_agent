# layer7_validation/ — 백테스트 파이프라인 (Layer 7)

백테스트의 핵심 조립 지점이다. PipelineRunner가 Layer 0~6 전체를 오케스트레이션하여 단일 종목 백테스트를 실행한다.

## 핵심 역할

- `PipelineRunner`: 7-Layer 시뮬레이션 루프 실행 (signal → target → order → fill → PnL)
- `BacktestConfig`: 설정 파싱/검증/직렬화 (flat + nested qlib-style 지원)
- `FillSimulator`: ChildOrder 체결 위임 (matching + impact + fee + bookkeeper)
- `ReportBuilder`: Layer 6 메트릭 조립 + 결과 저장 (JSON/CSV/plot)
- `ComponentFactory`: config에서 컴포넌트(fee model, slicer 등) 인스턴스화
- `ReproducibilityManager`: seed, config hash, code version 추적

## 대표 파일

| 파일 | 핵심 클래스 | 역할 |
|------|-----------|------|
| `pipeline_runner.py` | `PipelineRunner` | MarketState[] → BacktestResult (전체 루프) |
| `backtest_config.py` | `BacktestConfig`, `BacktestResult` | 설정 + 결과 데이터 클래스 |
| `fill_simulator.py` | `FillSimulator` | 체결 시뮬레이션 위임 (parent overfill 방지 포함) |
| `report_builder.py` | `ReportBuilder` | 리포트 생성 + 디스크 저장 (summary.json, CSV, plots) |
| `component_factory.py` | `ComponentFactory` | config → 컴포넌트 인스턴스 (fee/impact/latency/slicer/placement) |
| `reproducibility.py` | `ReproducibilityManager` | seed 설정, config hash, DataFrame hash, git version |

## PipelineRunner 실행 루프

```python
for state in states:
    # 1. 마이크로 이벤트 처리 (VI, halt)
    # 2. 미체결 주문 관리 (cancel/replace)
    # 3. Signal 생성 (Strategy.generate_signal)
    # 4. Target delta 계산 (RiskCaps, TurnoverBudget)
    # 5. ParentOrder 생성
    # 6. ChildOrder 분할 + 배치
    # 7. 체결 시뮬레이션 (FillSimulator)
    # 8. 계좌 갱신 + PnL 기록
```

## BacktestResult 산출물

- `summary.json`: 30+ 핵심 메트릭 (PnL, Sharpe, MDD, fill_rate 등)
- `config.json`: 사용된 BacktestConfig
- `pnl_series.csv`, `pnl_entries.csv`: PnL 상세
- `signals.csv`, `orders.csv`, `fills.csv`: 시뮬레이션 아티팩트
- `market_quotes.csv`: 시장 데이터
- `plots/`: 5종 시각화 (overview, signal, execution, dashboard, intraday_cumulative_profit)

## 전체 파이프라인에서의 위치

이 모듈이 **백테스트 실행의 최상위 조립 지점**이다. `scripts/backtest.py`와 `BacktestWorker`가 여기의 PipelineRunner를 호출한다.

## 주의사항

- BacktestConfig는 flat(하위 호환)과 nested(qlib-style) 설정 모두 지원
- `ComponentFactory`가 config enum 값에 따라 컴포넌트를 결정적으로 생성
- PipelineRunner는 O(1) running TWAP 계산으로 성능 최적화
- Parent overfill 방지가 FillSimulator에 내장됨

## 관련 문서

- [../layer6_evaluator/README.md](../layer6_evaluator/README.md) — 메트릭 계산
- [../orchestration/README.md](../orchestration/README.md) — Worker가 PipelineRunner를 호출
- [../../../../ADR.md](../../../../ADR.md) — ADR-007(PipelineRunner 분해)
