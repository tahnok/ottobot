# syntax=docker/dockerfile:1

# --- Build stage -----------------------------------------------------------
# Pin to a uv image that ships the project's Python (3.13). uv builds the
# project into a self-contained virtualenv at /app/.venv.
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first, in their own layer, so editing source doesn't
# bust the (slow) dependency cache. --locked uses uv.lock as the source of truth.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev

# Now bring in the project itself and install it. --no-editable installs
# ottobot into the venv's site-packages instead of linking back to /app/src;
# the runtime stage copies only /app/.venv, so an editable link would dangle
# and `import ottobot` would fail (ModuleNotFoundError: No module named 'ottobot').
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- Runtime stage ---------------------------------------------------------
# A bare Python image — no uv, no build tools — with just the venv copied in.
FROM python:3.14-slim-bookworm

# Run as an unprivileged user.
RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /app app

COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Put the venv's bin (which includes the `ottobot` console script) on PATH.
ENV PATH="/app/.venv/bin:$PATH"

USER app
WORKDIR /app

# Pass connection flags as arguments, e.g. `--tcp host:5000` or `--serial /dev/ttyUSB0`.
ENTRYPOINT ["ottobot"]
CMD ["--help"]
