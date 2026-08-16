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
| Model | Gemini 3.5 Pro (segment) / 3.5 Flash (outreach), Vertex AI |
| Agent framework | Google ADK |
| Compute | Cloud Run services + jobs |
| Async | Pub/Sub fan-out, one message per client, with DLQ |
| State | Cloud SQL (Postgres) + Alembic |
| Safety | Model Armor on CSV-derived free text |
| Trigger | Cloud Scheduler; Eventarc on GCS export upload |

## Status

Scaffold. Features are specified in [CLAUDE.md](CLAUDE.md) as `F-x` IDs and tracked in [GAPS_AND_ISSUES.md](GAPS_AND_ISSUES.md) as `GAP-xxx`. Start with [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md).

## Spin-up

```bash
python -m venv .venv && .venv/Scripts/activate    # Windows
pip install -e ".[dev,dashboard]"
cp .env.example .env                               # fill in GOOGLE_CLOUD_PROJECT
pytest
```

Cloud deployment: `PROJECT_ID=... ./deploy/deploy.sh` (see [deploy/deploy.sh](deploy/deploy.sh)).

## Data policy

This repository is public. **Only synthetic fixtures are committed.** No real client or prospect data reaches this repo, its demo video, or its deployment logs — `data/` and `*.csv` are gitignored, and the demo runs on a generated tenant.

## Disclosed prior work

Per hackathon rules, this project was newly created during the submission period (Aug 2026). It reuses deterministic, non-agentic domain logic — CSV column-synonym mapping, E.164/consent gates, CASL copy enforcement, attribution math — ported from the author's own private `relayops-prod` repository. All agent orchestration, the Pub/Sub fabric, the ADK implementation, the multi-tenant schema, and all Google Cloud deployment are new work. See [PROJECT_OVERVIEW.md](PROJECT_OVERVIEW.md) §9.

## License

MIT
