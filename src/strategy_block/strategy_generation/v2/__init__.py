"""Generation v2 — template-based and OpenAI plan-based v2 strategy generation."""

from .lowering import lower_plan_to_spec_v2, lower_to_spec_v2
from .templates_v2 import V2_TEMPLATES, get_v2_template
