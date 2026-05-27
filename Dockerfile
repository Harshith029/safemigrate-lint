# GitHub Action image for safemigrate-lint.
#
# python:3.11-slim base + uv (official binary, copied in) keeps the image
# small and the cold-start pull fast (the 30s end-to-end action budget is
# dominated by image pull on first use). uv sync --frozen pins the
# dependency tree to uv.lock so a tagged action version always installs
# the same pglast.
FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# Dependency-install layer: copy only the files uv needs to resolve. This
# layer stays cached unless pyproject.toml or uv.lock changes.
COPY pyproject.toml uv.lock README.md ./
COPY src/ ./src/

RUN uv sync --frozen --no-dev

# The venv's bin must precede the system PATH so `safemigrate-lint` and
# `python` resolve to the project's installed copy.
ENV PATH="/app/.venv/bin:$PATH"

COPY docker/entrypoint.py /entrypoint.py

ENTRYPOINT ["python", "/entrypoint.py"]
