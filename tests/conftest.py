"""Pytest configuration: --update-golden flag for regression test maintenance."""

from __future__ import annotations

import pytest


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help=(
            "Regenerate golden output files instead of comparing. Run after intentional "
            "analyzer changes; inspect the git diff before committing."
        ),
    )


@pytest.fixture
def update_golden(request: pytest.FixtureRequest) -> bool:
    return bool(request.config.getoption("--update-golden"))
