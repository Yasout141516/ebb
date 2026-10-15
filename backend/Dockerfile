# Build stage: resolve dependencies with uv into /app/.venv
FROM python:3.14-slim AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev --no-editable

# Runtime stage: just the virtualenv, run as a non-root user
FROM python:3.14-slim
RUN useradd --create-home --uid 1000 ebb
COPY --from=build /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1
USER ebb
WORKDIR /home/ebb
ENTRYPOINT ["ebb"]
CMD ["--help"]
