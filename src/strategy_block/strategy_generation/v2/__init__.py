"""Generation v2 — template-based v2 strategy generation with AST lowering."""

from .lowering import lower_to_spec_v2
from .templates_v2 import V2_TEMPLATES, get_v2_template
