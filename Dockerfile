# syntax=docker/dockerfile:1

# --- Build stage -----------------------------------------------------------
# The build and runtime stages MUST use the same Python image: the venv's
# site-packages lives under lib/pythonX.Y/, so a venv built on 3.14 can't be
# imported by 3.15 (ModuleNotFoundError: No module named 'ottobot'). Using the
# identical `python:` tag in both FROM lines lets Dependabot bump them together
# in one PR. uv itself is copied in from its own (separately versioned) image.
FROM python:3.14-slim-bookworm AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32 /uv /uvx /bin/

# Build against the image's own interpreter (never a uv-downloaded one), so
# the venv matches the runtime stage's Python exactly.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PYTHON=/usr/local/bin/python3

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
# Keep this tag identical to the build stage's (see note there).
FROM python:3.14-slim-bookworm

# tzdata provides the zoneinfo database (/usr/share/zoneinfo) that the C
# library reads when a TZ like `America/Toronto` is set, so log timestamps
# can be in local time. Without it, named zones silently fall back to UTC.
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt/lists \
    apt-get update && apt-get install -y --no-install-recommends tzdata

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
