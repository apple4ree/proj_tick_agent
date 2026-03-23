from .compiler import StrategyCompiler, CompiledStrategy
from .v2 import StrategyCompilerV2, CompiledStrategyV2

__all__ = [
    "StrategyCompiler", "CompiledStrategy",
    "StrategyCompilerV2", "CompiledStrategyV2",
    "compile_strategy",
]


def compile_strategy(spec) -> CompiledStrategy | CompiledStrategyV2:
    """Dispatch to the appropriate compiler based on spec type.

    Accepts either a StrategySpec (v1) or StrategySpecV2 (v2).
    Returns the corresponding compiled strategy that implements
    the Strategy ABC.
    """
    from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2
    from strategy_block.strategy_specs.schema import StrategySpec

    if isinstance(spec, StrategySpecV2):
        return StrategyCompilerV2.compile(spec)
    elif isinstance(spec, StrategySpec):
        return StrategyCompiler.compile(spec)
    else:
        raise TypeError(
            f"Cannot compile spec of type {type(spec).__name__}. "
            f"Expected StrategySpec or StrategySpecV2."
        )
