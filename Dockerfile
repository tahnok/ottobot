# syntax=docker/dockerfile:1

# --- Build stage -----------------------------------------------------------
# .python-version is the single source of truth for the Python version: CI's
# `uv python install` reads it, and so does this build. uv installs that
# Python into /python, then builds the project into a virtualenv at /app/.venv
# on top of it. Both stages use a plain Debian image (no Python of its own),
# so there's no second Python tag to drift out of sync with .python-version.
FROM debian:bookworm-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.13.8 /uv /uvx /bin/

# Only ever use the uv-managed Python in /python, never a system one, so the
# venv points at an interpreter the runtime stage copies over verbatim.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_INSTALL_DIR=/python \
    UV_PYTHON_PREFERENCE=only-managed

WORKDIR /app

# Install the Python named in .python-version. The patch release comes from
# the uv version above, so a Dependabot bump of uv also picks up Python fixes.
# Caveat: uv only knows Pythons released before it was, and will quietly pick
# a prerelease (e.g. 3.15.0b4) if that's all it knows — when bumping
# .python-version to a new minor, make sure the uv pin is recent enough.
#
# Then prune what a headless bot never uses (~70 MB): the standalone build's
# python binary is statically linked, so libpython*.so is only for embedding;
# Tcl/Tk/tkinter/IDLE are GUI; pip/ensurepip are unneeded since uv installs
# packages; headers and man pages are for building and reading, not running.
RUN --mount=type=bind,source=.python-version,target=.python-version \
    uv python install --no-bin \
    && cd /python/cpython-*/ \
    && rm -rf include share lib/libpython*.so* lib/libtcl* lib/itcl* lib/tcl* \
        lib/tk* lib/thread* lib/python3.*/site-packages/pip* \
        lib/python3.*/ensurepip lib/python3.*/idlelib lib/python3.*/tkinter \
        lib/python3.*/turtledemo lib/python3.*/lib-dynload/_tkinter*

WORKDIR /app

# Install dependencies first, in their own layer, so editing source doesn't
# bust the (slow) dependency cache. --locked uses uv.lock as the source of truth.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    --mount=type=bind,source=.python-version,target=.python-version \
    uv sync --locked --no-install-project --no-dev

# Now bring in the project itself and install it. --no-editable installs
# ottobot into the venv's site-packages instead of linking back to /app/src;
# the runtime stage copies only /app/.venv, so an editable link would dangle
# and `import ottobot` would fail (ModuleNotFoundError: No module named 'ottobot').
COPY .python-version pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-editable

# --- Runtime stage ---------------------------------------------------------
# A bare Debian image — no uv, no build tools — with just the uv-managed Python
# and the venv copied in. Keep this tag identical to the build stage's.
FROM debian:bookworm-slim

# tzdata provides the zoneinfo database (/usr/share/zoneinfo) that the C
# library reads when a TZ like `America/Toronto` is set, so log timestamps
# can be in local time. Without it, named zones silently fall back to UTC.
RUN --mount=type=cache,target=/var/cache/apt \
    --mount=type=cache,target=/var/lib/apt/lists \
    apt-get update && apt-get install -y --no-install-recommends tzdata

# Run as an unprivileged user.
RUN groupadd --system app && useradd --system --gid app --create-home --home-dir /app app

# The interpreter must land at the same path as in the builder: the venv's
# bin/python is a symlink into /python.
COPY --from=builder /python /python
COPY --from=builder --chown=app:app /app/.venv /app/.venv

# Put the venv's bin (which includes the `ottobot` console script) on PATH.
ENV PATH="/app/.venv/bin:$PATH"

USER app
WORKDIR /app

# Pass connection flags as arguments, e.g. `--tcp host:5000` or `--serial /dev/ttyUSB0`.
ENTRYPOINT ["ottobot"]
CMD ["--help"]
