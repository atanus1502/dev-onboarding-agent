# Runs the Slack app in Socket Mode: it connects out to Slack, so no port is exposed.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app
# Dependencies first, so code changes don't invalidate this layer.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 10001 agent && mkdir /data && chown agent /data
USER agent

# config/ (sources.yaml, identities.yaml) is mounted at runtime; secrets come from --env-file.
ENV ONBOARDING_SOURCES=/app/config/sources.yaml \
    ONBOARDING_IDENTITIES=/app/config/identities.yaml \
    ONBOARDING_DB=/data/onboarding.db
VOLUME /data

CMD ["onboarding-agent", "serve"]
