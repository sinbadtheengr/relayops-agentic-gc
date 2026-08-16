#!/usr/bin/env bash
# Provision and deploy the RelayOps Revenue Recovery Fleet from an empty
# Google Cloud project.
#
#   PROJECT_ID=my-project ./deploy/deploy.sh
#
# Idempotent: every step tolerates already-existing resources, so a failed run
# can be re-run rather than unpicked.
#
# Reproducibility is 30% of the hackathon score, so this script is a
# deliverable, not a convenience. Every step below was performed by hand first
# and written down immediately; the non-obvious ones carry the reason.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?set PROJECT_ID}"
REGION="${REGION:-us-central1}"          # where the containers run
VERTEX_LOCATION="${VERTEX_LOCATION:-global}"  # where Gemini >=3.5 is served
SQL_INSTANCE="${SQL_INSTANCE:-relayops-fleet-db}"
DB_NAME="${DB_NAME:-relayops}"
DB_USER="${DB_USER:-relayops}"
SECRET_ID="${SECRET_ID:-relayops-db-password}"
TOPIC="${TOPIC:-relayops.campaign.run}"
DLQ_TOPIC="${DLQ_TOPIC:-relayops.campaign.run.dlq}"
SUBSCRIPTION="${SUBSCRIPTION:-relayops.campaign.run.worker}"
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/relayops/fleet:latest"

say() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

# ---------------------------------------------------------------------------
say "1. Enable APIs"
gcloud services enable \
  aiplatform.googleapis.com run.googleapis.com sqladmin.googleapis.com \
  pubsub.googleapis.com cloudscheduler.googleapis.com eventarc.googleapis.com \
  cloudbuild.googleapis.com artifactregistry.googleapis.com \
  logging.googleapis.com secretmanager.googleapis.com modelarmor.googleapis.com \
  --project "$PROJECT_ID"

# ---------------------------------------------------------------------------
say "1b. Point Application Default Credentials at this project"
# NOT optional and NOT obvious. Without it every Vertex call returns
#   403 PERMISSION_DENIED: Permission 'aiplatform.endpoints.predict' denied
# even for a project owner, because ADC still bills quota to whichever project
# it was last pointed at. It presents as an IAM problem and is not one.
gcloud auth application-default set-quota-project "$PROJECT_ID"

# ---------------------------------------------------------------------------
say "2. Cloud SQL (Postgres 16)"
if ! gcloud sql instances describe "$SQL_INSTANCE" --project "$PROJECT_ID" >/dev/null 2>&1; then
  # db-f1-micro + HDD + no backups is the cheapest instance that runs this
  # workload. Creation takes 10-20 minutes; the instance does NOT scale to
  # zero, so delete it when the project is idle.
  gcloud sql instances create "$SQL_INSTANCE" --project "$PROJECT_ID" \
    --database-version=POSTGRES_16 --edition=ENTERPRISE --tier=db-f1-micro \
    --region="$REGION" --storage-size=10GB --storage-type=HDD --no-backup --quiet
fi
INSTANCE_CONN="$(gcloud sql instances describe "$SQL_INSTANCE" --project "$PROJECT_ID" \
  --format='value(connectionName)')"

# ---------------------------------------------------------------------------
say "3. Database, role, password secret, and DATABASE_URL"
# Generates the password, stores it in Secret Manager, authorizes this
# machine's IP for migrations, and writes .env. Never prints the password.
python scripts/setup_cloudsql.py

# ---------------------------------------------------------------------------
say "3b. Operator password for the approval dashboard"
# The dashboard refuses to serve without this, by design — it lists client
# names, phone numbers and message copy.
if ! gcloud secrets describe relayops-dashboard-password --project "$PROJECT_ID" >/dev/null 2>&1; then
  gcloud secrets create relayops-dashboard-password --project "$PROJECT_ID" \
    --replication-policy automatic --quiet
  python -c "import secrets;print(secrets.token_urlsafe(24),end='')" \
    | gcloud secrets versions add relayops-dashboard-password --project "$PROJECT_ID" --data-file=- --quiet
  echo "  generated. Read it with:"
  echo "  gcloud secrets versions access latest --secret relayops-dashboard-password --project $PROJECT_ID"
fi

say "4. Apply migrations"
python -m alembic upgrade head

# ---------------------------------------------------------------------------
say "5. Service accounts — one per role, least privilege"
create_sa() {
  gcloud iam service-accounts create "$1" --project "$PROJECT_ID" \
    --display-name "$2" 2>/dev/null || true
}
create_sa relayops-publisher "RelayOps publisher job"
create_sa relayops-worker    "RelayOps campaign worker"
create_sa relayops-dashboard "RelayOps approval dashboard"

grant() {
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member "serviceAccount:$1@${PROJECT_ID}.iam.gserviceaccount.com" \
    --role "$2" --condition=None --quiet >/dev/null
}
grant relayops-publisher roles/pubsub.publisher
grant relayops-publisher roles/cloudsql.client
grant relayops-publisher roles/secretmanager.secretAccessor

grant relayops-worker roles/cloudsql.client
grant relayops-worker roles/aiplatform.user
grant relayops-worker roles/pubsub.publisher          # for the DLQ
grant relayops-worker roles/secretmanager.secretAccessor
grant relayops-worker roles/logging.logWriter

grant relayops-dashboard roles/cloudsql.client
grant relayops-dashboard roles/secretmanager.secretAccessor
# NOTE: relayops-dashboard deliberately has NO aiplatform.user. The approval
# surface approves; it does not generate. If it is ever able to call a model,
# the human gate has a bypass.

# ---------------------------------------------------------------------------
say "6. Pub/Sub topics, DLQ and push subscription"
gcloud pubsub topics create "$TOPIC" --project "$PROJECT_ID" 2>/dev/null || true
gcloud pubsub topics create "$DLQ_TOPIC" --project "$PROJECT_ID" 2>/dev/null || true

# ---------------------------------------------------------------------------
say "7. Build the image"
gcloud artifacts repositories create relayops --project "$PROJECT_ID" \
  --repository-format=docker --location="$REGION" 2>/dev/null || true
gcloud builds submit --project "$PROJECT_ID" --tag "$IMAGE" --quiet

# ---------------------------------------------------------------------------
say "8. Deploy Cloud Run services"
# Cloud Run reaches Cloud SQL over its Unix socket, so no IP allow-listing and
# no connector library. GOOGLE_CLOUD_LOCATION is set to the VERTEX location,
# NOT the run region: Gemini >=3.5 is served only on the global endpoint, and
# a region here yields 404 NOT_FOUND on a model that models.list() shows.
# config.py assembles the DSN from CLOUD_SQL_INSTANCE + DB_USER + DB_NAME and
# the DB_PASSWORD secret, so only the password lives in Secret Manager.
gcloud run deploy relayops-worker --project "$PROJECT_ID" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "relayops-worker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --add-cloudsql-instances "$INSTANCE_CONN" \
  --set-secrets "DB_PASSWORD=${SECRET_ID}:latest" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},GOOGLE_CLOUD_LOCATION=${VERTEX_LOCATION},GOOGLE_GENAI_USE_VERTEXAI=true,CLOUD_SQL_INSTANCE=${INSTANCE_CONN},DB_NAME=${DB_NAME},DB_USER=${DB_USER},PUBSUB_DLQ_TOPIC=${DLQ_TOPIC},DRY_RUN=false" \
  --args "uvicorn,relayops_fleet.fabric.worker:app,--host,0.0.0.0,--port,8080" \
  --no-allow-unauthenticated --quiet

gcloud run deploy relayops-dashboard --project "$PROJECT_ID" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "relayops-dashboard@${PROJECT_ID}.iam.gserviceaccount.com" \
  --add-cloudsql-instances "$INSTANCE_CONN" \
  --set-secrets "DB_PASSWORD=${SECRET_ID}:latest,DASHBOARD_PASSWORD=relayops-dashboard-password:latest" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},CLOUD_SQL_INSTANCE=${INSTANCE_CONN},DB_NAME=${DB_NAME},DB_USER=${DB_USER}" \
  --args "uvicorn,relayops_fleet.dashboard.app:app,--host,0.0.0.0,--port,8080" \
  --no-allow-unauthenticated --quiet

WORKER_URL="$(gcloud run services describe relayops-worker --project "$PROJECT_ID" \
  --region "$REGION" --format='value(status.url)')"

# ---------------------------------------------------------------------------
say "8b. Push subscription with dead-lettering"
# max-delivery-attempts is a backstop. The worker already publishes poison
# messages to the DLQ itself and acks them, so this only catches failures the
# handler never got to run for.
gcloud pubsub subscriptions create "$SUBSCRIPTION" --project "$PROJECT_ID" \
  --topic "$TOPIC" \
  --push-endpoint "$WORKER_URL" \
  --push-auth-service-account "relayops-worker@${PROJECT_ID}.iam.gserviceaccount.com" \
  --dead-letter-topic "$DLQ_TOPIC" \
  --max-delivery-attempts 5 \
  --ack-deadline 600 \
  2>/dev/null || true

# ---------------------------------------------------------------------------
say "9. Publisher job + nightly schedule"
gcloud run jobs deploy relayops-publisher --project "$PROJECT_ID" --region "$REGION" \
  --image "$IMAGE" \
  --service-account "relayops-publisher@${PROJECT_ID}.iam.gserviceaccount.com" \
  --set-cloudsql-instances "$INSTANCE_CONN" \
  --set-secrets "DB_PASSWORD=${SECRET_ID}:latest" \
  --set-env-vars "GOOGLE_CLOUD_PROJECT=${PROJECT_ID},CLOUD_SQL_INSTANCE=${INSTANCE_CONN},DB_NAME=${DB_NAME},DB_USER=${DB_USER},PUBSUB_TOPIC_CAMPAIGN_RUN=${TOPIC},DRY_RUN=false" \
  --command "python" --args "-m,relayops_fleet.fabric.publisher" \
  --quiet

gcloud scheduler jobs create http relayops-nightly --project "$PROJECT_ID" \
  --location "$REGION" \
  --schedule "0 9 * * *" --time-zone "America/Toronto" \
  --uri "https://${REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${PROJECT_ID}/jobs/relayops-publisher:run" \
  --http-method POST \
  --oauth-service-account-email "relayops-publisher@${PROJECT_ID}.iam.gserviceaccount.com" \
  2>/dev/null || true

# ---------------------------------------------------------------------------
say "10. Seed the synthetic demo tenant"
# Synthetic data only. Never a real clinic's client list — this project's
# logs and this repo are both visible to people who are not the clinic.
python scripts/seed_demo_tenant.py

say "Done"
cat <<SUMMARY
  worker      $WORKER_URL   (authenticated)
  dashboard   $(gcloud run services describe relayops-dashboard --project "$PROJECT_ID" --region "$REGION" --format='value(status.url)')
  schedule    relayops-nightly, 09:00 America/Toronto

Trigger a run now:
  gcloud run jobs execute relayops-publisher --project $PROJECT_ID --region $REGION

Remove the development IP allow-list entry before demoing:
  gcloud sql instances patch $SQL_INSTANCE --project $PROJECT_ID --clear-authorized-networks
SUMMARY
