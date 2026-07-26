"""safemigrate-lint — open-source Postgres migration linter."""

from importlib.metadata import PackageNotFoundError, version

try:
    # Single source of truth is pyproject's [project].version — read it at import
    # time so `__version__` can't drift from the released version again.
    __version__ = version("safemigrate-lint")
except PackageNotFoundError:  # running from a source tree with no install
    __version__ = "0.0.0.dev0"

__all__ = ["__version__"]
