"""Rule registration entry point.

Importing this module triggers every rule's `@register_rule` decorator to
populate `RULES`. Adding a rule = drop a file in this package, then add the
import below.
"""

from __future__ import annotations

# Rule modules — imported for side effect (decorator registration).
from . import (
    add_non_nullable_without_default,  # noqa: F401
    concurrent_index_create_required,  # noqa: F401
    concurrent_index_drop_required,  # noqa: F401
    drop_column_restricted,  # noqa: F401
    drop_database_restricted,  # noqa: F401
    drop_table_restricted,  # noqa: F401
    nullable_to_non_nullable_may_fail,  # noqa: F401
    rename_column_warning,  # noqa: F401
    rename_table_warning,  # noqa: F401
)
from ._registry import RULES, Rule, RuleContext, register_rule

__all__ = ["RULES", "Rule", "RuleContext", "register_rule"]
