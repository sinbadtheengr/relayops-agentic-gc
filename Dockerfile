# One image, three entrypoints: the worker service, the dashboard service, and
# the publisher job. They share all their code, so three images would only be
# three chances for them to drift apart.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
# The approved campaign copy. core/templates.py resolves it relative to the
# repo root, so it must sit beside src/, not inside the package.
COPY templates ./templates
COPY alembic.ini ./
COPY alembic ./alembic
COPY scripts ./scripts

RUN pip install --no-cache-dir ".[dashboard]"

# Fail the BUILD if the approved campaign copy is missing.
#
# It went missing once: .gcloudignore excluded *.md as documentation, which
# silently dropped templates/campaign-templates.md from the build context.
# The image started fine, served health checks fine, and only failed at draft
# time — after the segment model call had already been paid for. A build that
# cannot produce a draft should never reach a registry.
RUN test -s /app/templates/campaign-templates.md \
    && grep -q "## Segment D" /app/templates/campaign-templates.md \
    || (echo "FATAL: approved campaign templates missing or incomplete" && exit 1)

# The package is installed non-editable, so the repo-relative walk in
# core/templates.py would resolve under site-packages. Point it at the copy
# baked into the image instead — a missing template only surfaces at draft
# time, which is far too late.
ENV RELAYOPS_TEMPLATES_DIR=/app/templates

# Cloud Run injects PORT; default for local runs.
ENV PORT=8080

# Overridden per service at deploy time (see deploy/deploy.sh).
CMD exec uvicorn relayops_fleet.fabric.worker:app --host 0.0.0.0 --port ${PORT}
