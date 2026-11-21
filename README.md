> **ARCHIVED — superseded by relayops-prod**

# RelayOps Revenue Recovery Fleet

**A multi-tenant background agent fleet that turns a med spa's booking-software export into approved, CASL-compliant win-back campaigns — and into a defensible invoice.**

Built for the [All Things Agentic Hackathon](https://allthingsagentichackathon.devpost.com/) (Google Cloud), August 2026.

Small clinics lose more revenue to clients who quietly stop coming back than to any other cause. The list of those clients is already sitting in their booking software. What is missing is the nightly work of deciding who is worth contacting, what to say to each of them, and proving afterwards which visits the campaign actually caused.

Every night, per tenant clinic, this fleet fans out **one agent run per lapsed client**: gate → segment → draft → queue for human approval.

## The design claim

Most agent demos are impressive because of what the agent does. This one is credible because of **what the agent is forbidden to do**.

- **It cannot decide who may be contacted.** Consent, opt-out, suppression and cooldown are pure-Python gates that run before any model call. A gated client never reaches Gemini and costs nothing.
- **It cannot do arithmetic.** Lapse buckets, VIP percentiles and billing are computed in Python and handed to the model as authoritative facts.
- **It cannot send.** There is no send path in this repository. Agents produce drafts; a human approves; a human sends.
- **It cannot act unlogged.** Every model call writes an `agent_decisions` row before its output is allowed to affect anything.

That boundary is not caution for its own sake. This system writes to strangers under Canada's Anti-Spam Legislation on behalf of a clinic that owns the relationship, and its output becomes an invoice someone will dispute.

## Architecture

```
Cloud Scheduler (nightly, per tenant)
  └─> Cloud Run Job: publisher ──> Pub/Sub  relayops.campaign.run  ──> DLQ
                                      └─> Cloud Run Service: worker
                                            ├─ core.gates      (no LLM)
                                            ├─ ADK segment  → Gemini 3.5 Pro
                                            ├─ ADK outreach → Gemini 3.5 Flash
                                            ├─ core.casl       (no LLM)
                                            └─> Cloud SQL (Postgres)
                                                 + agent_decisions
Cloud Run Service: approval dashboard ──> same Cloud SQL   (no model access)
```

| Layer | Choice |
|---|---|
| Model | Gemini 3.7 Flash (segment) / 3.5 Flash (outreach), Vertex AI `global` endpoint |
| Agent framework | Google ADK |
| Compute | Cloud Run services + jobs |
| Async | Pub/Sub fan-out, one message per client, with DLQ |
| State | Cloud SQL (Postgres) + Alembic |
| Safety | Model Armor on CSV-derived free text |
| Memory | Agent Engine Memory Bank, scoped per clinic |
| Trigger | Cloud Scheduler; Eventarc on GCS export upload |

## Status

Built and deployed on Google Cloud. Every specified feature is implemented; what remains is the submission itself (video, console screenshots, Devpost entry).

Features are specified in [CLAUDE.md](CLAUDE.md) as `F-x` IDs and tracked in [GAPS_AND_ISSUES.md](GAPS_AND_ISSUES.md) as `GAP-xxx`. Start with [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).

## Spin-up

```bash
python -m venv .venv && .venv/Scripts/activate    # Windows
pip install -e ".[dev,dashboard]"
cp .env.example .env                               # fill in GOOGLE_CLOUD_PROJECT
pytest
```

Run the tests that need a database (Cloud SQL or any Postgres with the migrations applied):

```bash
RELAYOPS_TEST_DB=1 pytest
```

## Cloud deployment

```bash
PROJECT_ID=your-project ./deploy/deploy.sh
```

One script, from an empty project: enables APIs, creates Cloud SQL, generates the DB and dashboard passwords into Secret Manager, applies migrations, creates three least-privilege service accounts, builds the image, deploys the worker and dashboard services plus the publisher job, wires the Pub/Sub push subscription with dead-lettering, schedules the nightly run, and seeds the synthetic demo tenant.

Three things in it are easy to get wrong and are commented in place:

- **`gcloud auth application-default set-quota-project`** is mandatory. Without it every Vertex call returns `403 PERMISSION_DENIED` even for a project owner, because ADC still bills quota elsewhere. It presents as an IAM problem and is not one.
- **`GOOGLE_CLOUD_LOCATION=global`, not the Cloud Run region.** Gemini ≥3.5 is served only on the global endpoint; a region there returns 404 for a model `models.list()` happily shows.
- **The dashboard service account has no `aiplatform.user`.** The approval surface approves; it does not generate. If it could call a model, the human gate would have a bypass.

Trigger a run immediately instead of waiting for the schedule:

```bash
gcloud run jobs execute relayops-publisher --project $PROJECT_ID --region us-central1
```

## Data policy

This repository is public. **Only synthetic fixtures are committed.** No real client or prospect data reaches this repo, its demo video, or its deployment logs — `data/` and `*.csv` are gitignored, and the demo runs on a generated tenant.

## Disclosed prior work

Per hackathon rules, this project was newly created during the submission period (Aug 2026). It reuses deterministic, non-agentic domain logic — CSV column-synonym mapping, E.164/consent gates, CASL copy enforcement, attribution math — ported from the author's own private `relayops-prod` repository. All agent orchestration, the Pub/Sub fabric, the ADK implementation, the multi-tenant schema, and all Google Cloud deployment are new work. See [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) §9.

## License

MIT

## Marketing consent and withdrawals

Apply the Alembic migration before starting the updated workers or dashboard. Existing records start without evidence. Record evidence for the actual sending clinic and each email address or phone number. A visit date by itself is insufficient. Keep the original consent record or invoice referenced by the evidence. Purchase and inquiry records expire after two calendar years and six calendar months respectively. At a month-end boundary the service withholds on the anniversary date. Express consent continues until withdrawal. Use publication only for a role-relevant message to an address published by or for the recipient without a solicitation restriction. Retain a dated copy of that publication.

Use a monitored two-way sending number and reply inbox. Record all withdrawal requests immediately and reconcile both channels with the suppression register. Verify with controlled recipients after deployment. Keep unsubscribe and contact mechanisms available for at least 60 days after delivery and process withdrawals without delay, within 10 business days. A downloaded CSV is a point-in-time export. Recheck eligibility before any later external send.

[CRTC guidance](https://crtc.gc.ca/eng/com500/faq500.htm)

Use `python -m relayops_fleet.db.marketing_consent --help` to record a clinic grant and `python -m relayops_fleet.db.clinic_identity --help` for the sending clinic’s address and monitored contact. Re-review outstanding approvals after migration. The fleet workflow produces drafts for manual delivery.

Use `python -m relayops_fleet.db.consent_repo --help` to immediately suppress a reply or STOP request in the fleet database.
