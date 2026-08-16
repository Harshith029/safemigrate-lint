# GitHub Action image for safemigrate-lint.
#
# python:3.11-slim base + uv (official binary, copied in) keeps the image
# small and the cold-start pull fast (the 30s end-to-end action budget is
# dominated by image pull on first use). uv sync --frozen pins the
# dependency tree to uv.lock so a tagged action version always installs
# the same pglast.
# Pinned by digest, not just tag: a tag is mutable, so `python:3.11-slim` can
# resolve to a different image tomorrow and a tagged action version would stop
# building reproducibly. Bump deliberately.
FROM python:3.11-slim@sha256:a630a63cdb314e2d138a2fca3e375e319e8568346ffafac5b980f888630ac4f1

# Same reasoning for uv — pinned to an exact release rather than :latest.
COPY --from=ghcr.io/astral-sh/uv:0.11.16 /uv /usr/local/bin/uv

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

# Drop root. This container parses SQL written by whoever opened the pull
# request, using a native parser, while holding a token that can write PR
# comments and checks — no part of that needs root. The workspace GitHub mounts
# stays readable; the lint only ever reads it.
RUN useradd --create-home --uid 1001 linter
USER linter

ENTRYPOINT ["python", "/entrypoint.py"]
