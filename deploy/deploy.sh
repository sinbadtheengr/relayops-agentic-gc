#!/usr/bin/env bash
# One-command provision + deploy. Reproducibility is 30% of the hackathon
# score, so this script is a deliverable, not a convenience.
#
# Usage:  PROJECT_ID=relayops-fleet ./deploy/deploy.sh
#
# TODO(F-12): fill in. Ordered so each step's output feeds the next.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"          # where containers run
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"  # where Gemini >=3.5 is served

# Verified 2026-08-15/16 against a real empty project. See
# docs/F1-qualification-evidence.md.

echo "==> 1. Enable APIs"
gcloud services enable \
  aiplatform.googleapis.com run.googleapis.com sqladmin.googleapis.com \
  pubsub.googleapis.com cloudscheduler.googleapis.com eventarc.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com \
  logging.googleapis.com secretmanager.googleapis.com \
  --project "$PROJECT_ID"

echo "==> 1b. Point ADC at this project"
# NOT optional. Without it every Vertex call returns
#   403 PERMISSION_DENIED: Permission 'aiplatform.endpoints.predict' denied
# even for a project owner, because ADC still bills quota to the old project.
gcloud auth application-default set-quota-project "$PROJECT_ID"

echo "==> 2. Cloud SQL (Postgres) instance + database + user"

echo "==> 3. Secrets -> Secret Manager (never baked into an image)"

echo "==> 4. Pub/Sub topic + DLQ + push subscription"

echo "==> 5. Service accounts, one per role, least privilege:"
#        sa-publisher : pubsub.publisher, cloudsql.client
#        sa-worker    : pubsub.subscriber, cloudsql.client, aiplatform.user
#        sa-dashboard : cloudsql.client                       (NO aiplatform)
#        The dashboard cannot call a model. It approves; it does not generate.

echo "==> 6. Alembic migrations against Cloud SQL"

echo "==> 7. Deploy Cloud Run: worker service, dashboard service, publisher job"

echo "==> 8. Cloud Scheduler nightly trigger -> publisher job"

echo "==> 9. Seed the synthetic demo tenant (never real client data)"
