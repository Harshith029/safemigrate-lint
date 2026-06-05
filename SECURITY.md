# Security Policy

## Reporting a vulnerability

Please report security issues **privately** — don't open a public issue.

Use GitHub's private vulnerability reporting: the repository's **Security** tab →
**Report a vulnerability**. That opens a private advisory only the maintainers
can see. Include what you found, how to reproduce it, and the impact; we'll
acknowledge within a few days.

## Scope

safemigrate-lint is a static analyzer — it parses SQL text and never connects to
a database. The most relevant surfaces are the **GitHub Action** (which runs in
your CI with `GITHUB_TOKEN`), the **Docker image**, and the **dependency chain**.
Reports about token handling, the container, or supply-chain concerns are
especially welcome.

## Supported versions

Fixes are released against the latest version on PyPI and the moving `@v1` tag.
