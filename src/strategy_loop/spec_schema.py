"""
strategy_loop/spec_schema.py
-----------------------------
Pydantic models for the simple strategy spec.

전략 생성 시 OpenAI structured output (beta.chat.completions.parse)의
response_format으로 사용된다.

BoolExpr는 재귀 구조이므로 forward reference + model_rebuild() 패턴을 사용.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, Field

Op = Literal[">", ">=", "<", "<=", "==", "!="]
Side = Literal["long", "short"]


# ── ValueExpr ─────────────────────────────────────────────────────────────────

class FeatureExpr(BaseModel):
    type: Literal["feature"]
    name: str


class ConstExpr(BaseModel):
    type: Literal["const"]
    value: float


class PositionAttrExpr(BaseModel):
    type: Literal["position_attr"]
    name: Literal["holding_ticks"]


ValueExpr = Annotated[
    Union[FeatureExpr, ConstExpr, PositionAttrExpr],
    Field(discriminator="type"),
]


# ── BoolExpr ──────────────────────────────────────────────────────────────────

class ComparisonExpr(BaseModel):
    type: Literal["comparison"]
    op: Op
    # shorthand: feature + threshold  (시장 피처 비교)
    feature: Optional[str] = None
    threshold: Optional[float] = None
    # full form: left + right  (holding_ticks 같은 position_attr 비교)
    left: Optional[ValueExpr] = None
    right: Optional[ValueExpr] = None


class AllExpr(BaseModel):
    type: Literal["all"]
    conditions: list["BoolExpr"]  # forward ref


class AnyExpr(BaseModel):
    type: Literal["any"]
    conditions: list["BoolExpr"]  # forward ref


class NotExpr(BaseModel):
    type: Literal["not"]
    condition: "BoolExpr"  # forward ref


# BoolExpr는 위 4개 클래스 정의 이후에 배치해야 한다
BoolExpr = Annotated[
    Union[ComparisonExpr, AllExpr, AnyExpr, NotExpr],
    Field(discriminator="type"),
]

# forward reference 해소
AllExpr.model_rebuild()
AnyExpr.model_rebuild()
NotExpr.model_rebuild()


# ── Top-level spec ─────────────────────────────────────────────────────────────

class EntrySpec(BaseModel):
    side: Side
    condition: BoolExpr
    size: int = Field(gt=0)


class ExitSpec(BaseModel):
    condition: BoolExpr


class RiskSpec(BaseModel):
    max_position: int = Field(gt=0)


class StrategySpec(BaseModel):
    name: str
    entry: EntrySpec
    exit: ExitSpec
    risk: RiskSpec
