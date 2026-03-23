"""StrategySpec v2 — hierarchical strategy IR with expression AST."""

from .ast_nodes import (
    ExprNode,
    ConstExpr,
    FeatureExpr,
    ComparisonExpr,
    AllExpr,
    AnyExpr,
    NotExpr,
    CrossExpr,
    LagExpr,
    RollingExpr,
    PersistExpr,
    expr_from_dict,
)
from .schema_v2 import (
    EntryPolicyV2,
    ExitActionV2,
    ExitRuleV2,
    ExitPolicyV2,
    PositionSizingV2,
    RiskPolicyV2,
    PreconditionV2,
    RegimeV2,
    ExecutionPolicyV2,
    StrategySpecV2,
)
