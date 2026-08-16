"""Environment-driven settings.

Reads the repo-root .env once (real environment variables take precedence),
then exposes a frozen Settings object. Copy .env.example to .env and fill in
real values — .env is gitignored and must never be committed.

Same shape as relayops-prod's `relayops.config` so the ported deterministic
core needs no rewiring. See PROJECT_OVERVIEW.md §"Disclosed prior work".
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[2]


def _flag(name: str, default: str = "false") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    # Google Cloud
    google_cloud_project: str
    google_cloud_location: str
    use_vertexai: bool
    # Models
    gemini_segment_model: str
    gemini_outreach_model: str
    # Storage
    database_url: str
    cloud_sql_instance: str
    # Async fabric
    pubsub_topic_campaign_run: str
    pubsub_subscription_campaign_run: str
    pubsub_dlq_topic: str
    # Caps
    segment_max_clients: int
    outreach_max_drafts: int
    dry_run: bool
    # Compliance
    contact_cooldown_days: int
    attribution_window_days: int
    show_fee_cents: int
    fee_cap_cents: int
    # Operator surfaces
    dashboard_password: str
    public_base_url: str
    model_armor_template: str


@lru_cache
def get_settings() -> Settings:
    load_dotenv(REPO_ROOT / ".env")
    return Settings(
        google_cloud_project=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        # "global", not a region: Gemini >=3.5 is only served on the global
        # endpoint (F-1, 2026-08-15). A region here yields 404 NOT_FOUND on
        # generate_content even though models.list() shows the model.
        google_cloud_location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        use_vertexai=_flag("GOOGLE_GENAI_USE_VERTEXAI", "true"),
        gemini_segment_model=os.environ.get("GEMINI_SEGMENT_MODEL", "gemini-3.7-flash"),
        gemini_outreach_model=os.environ.get("GEMINI_OUTREACH_MODEL", "gemini-3.5-flash"),
        database_url=os.environ.get(
            "DATABASE_URL", "postgresql+psycopg://relayops:relayops@localhost:5434/relayops"
        ),
        cloud_sql_instance=os.environ.get("CLOUD_SQL_INSTANCE", "").strip(),
        pubsub_topic_campaign_run=os.environ.get(
            "PUBSUB_TOPIC_CAMPAIGN_RUN", "relayops.campaign.run"
        ),
        pubsub_subscription_campaign_run=os.environ.get(
            "PUBSUB_SUBSCRIPTION_CAMPAIGN_RUN", "relayops.campaign.run.worker"
        ),
        pubsub_dlq_topic=os.environ.get("PUBSUB_DLQ_TOPIC", "relayops.campaign.run.dlq"),
        segment_max_clients=int(os.environ.get("SEGMENT_MAX_CLIENTS", "25")),
        outreach_max_drafts=int(os.environ.get("OUTREACH_MAX_DRAFTS", "25")),
        # Dry run defaults ON: an uncapped live fan-out is the expensive mistake.
        dry_run=_flag("DRY_RUN", "true"),
        contact_cooldown_days=int(os.environ.get("CONTACT_COOLDOWN_DAYS", "14")),
        attribution_window_days=int(os.environ.get("ATTRIBUTION_WINDOW_DAYS", "30")),
        show_fee_cents=int(os.environ.get("SHOW_FEE_CENTS", "5000")),
        fee_cap_cents=int(os.environ.get("FEE_CAP_CENTS", "150000")),
        dashboard_password=os.environ.get("DASHBOARD_PASSWORD", ""),
        public_base_url=os.environ.get("PUBLIC_BASE_URL", "").rstrip("/"),
        model_armor_template=os.environ.get("MODEL_ARMOR_TEMPLATE", "").strip(),
    )
