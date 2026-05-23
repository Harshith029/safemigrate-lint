"""Rule registration entry point.

Importing this module triggers every rule's `@register_rule` decorator to
populate `RULES`. Adding a rule = drop a file in this package, then add the
import below.
"""

from __future__ import annotations

from ._registry import RULES, Rule, RuleContext, register_rule

# Rule modules — imported for side effect (decorator registration).
from . import concurrent_index_create_required  # noqa: F401
from . import drop_column_restricted  # noqa: F401
from . import drop_table_restricted  # noqa: F401

__all__ = ["RULES", "Rule", "RuleContext", "register_rule"]
