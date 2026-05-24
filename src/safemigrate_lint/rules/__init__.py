"""Rule registration entry point.

Importing this module triggers every rule's `@register_rule` decorator to
populate `RULES`. Adding a rule = drop a file in this package, then add the
import below.
"""

from __future__ import annotations

# Rule modules — imported for side effect (decorator registration).
from . import (
    access_method_change_rewrites,  # noqa: F401
    add_non_nullable_without_default,  # noqa: F401
    column_type_change_rewrites_table,  # noqa: F401
    concurrent_index_create_required,  # noqa: F401
    concurrent_index_drop_required,  # noqa: F401
    constraint_dropped_warning,  # noqa: F401
    constraint_not_valid_required,  # noqa: F401
    drop_column_restricted,  # noqa: F401
    drop_database_restricted,  # noqa: F401
    drop_table_restricted,  # noqa: F401
    identity_column_add_rewrites,  # noqa: F401
    index_concurrent_in_transaction_banned,  # noqa: F401
    nullable_to_non_nullable_may_fail,  # noqa: F401
    pk_constraint_exclusive_lock,  # noqa: F401
    rename_column_warning,  # noqa: F401
    rename_table_warning,  # noqa: F401
    stored_generated_column_rewrites,  # noqa: F401
    table_logging_mode_rewrites,  # noqa: F401
    transaction_nesting_banned,  # noqa: F401
    trigger_add_blocks_writes,  # noqa: F401
    unique_constraint_data_dependent,  # noqa: F401
    unique_constraint_exclusive_lock,  # noqa: F401
    volatile_default_rewrites_table,  # noqa: F401
)
from ._registry import RULES, Rule, RuleContext, register_rule

__all__ = ["RULES", "Rule", "RuleContext", "register_rule"]
