from .v2 import StrategyCompilerV2, CompiledStrategyV2

__all__ = [
    "StrategyCompilerV2", "CompiledStrategyV2", "compile_strategy",
]


def compile_strategy(spec) -> CompiledStrategyV2:
    """Compile StrategySpecV2 into an executable v2 strategy."""
    from strategy_block.strategy_specs.v2.schema_v2 import StrategySpecV2

    if not isinstance(spec, StrategySpecV2):
        raise TypeError(
            f"Cannot compile spec of type {type(spec).__name__}. "
            "Expected StrategySpecV2."
        )
    return StrategyCompilerV2.compile(spec)
