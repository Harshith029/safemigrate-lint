<!-- One or two sentences on what this changes. For a new rule, name the production scenario it prevents. -->

## Checklist

- [ ] `uv run pytest` passes
- [ ] `uv run ruff check src/ tests/ docker/` and `uv run mypy src/ docker/` are clean
- [ ] If rule output changed: regenerated goldens (`uv run pytest --update-golden`) and reviewed the diff
- [ ] New rule? Added a fixture that fires **and** a `safe_` case that doesn't, plus a trigger in `tests/unit/test_rules_fire.py`

<!-- See CONTRIBUTING.md and docs/writing-a-rule.md. -->
