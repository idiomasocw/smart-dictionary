# syntax=docker/dockerfile:1
# The backend image (design §11): runtime dependencies only, run by an unprivileged user.
# CI builds it for linux/arm64 (the t4g host); `docker build backend` works on x86 too.
# The entry point is the CLI, so the same image runs `api`, `migrate` and later `worker`.

FROM ghcr.io/astral-sh/uv:0.11.7 AS uv

FROM python:3.13-slim-trixie AS build
COPY --from=uv /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Dependencies first, so a code change doesn't reinstall them. No dev or data groups.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups --no-install-project
COPY README.md alembic.ini ./
COPY migrations ./migrations
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-default-groups

FROM python:3.13-slim-trixie
RUN useradd --system --uid 10001 --home-dir /app --shell /usr/sbin/nologin app
WORKDIR /app
# Root owns the code; the app user can read it but not change it.
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
USER app
EXPOSE 8000
ENTRYPOINT ["smart-dictionary"]
CMD ["api", "--host", "0.0.0.0", "--port", "8000"]
