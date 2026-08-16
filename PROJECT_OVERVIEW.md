# PROJECT_OVERVIEW — RelayOps Revenue Recovery Fleet

> Companion documents: [GAPS_AND_ISSUES.md](GAPS_AND_ISSUES.md) (severity-ranked build register) and [CLAUDE.md](CLAUDE.md) (implementation specs). Gap IDs (`GAP-xxx`) and feature IDs (`F-x`) cross-reference between all three files. Strategy rationale lives in [PROPOSAL.md](PROPOSAL.md).

## 1. Single job

**Ship a qualifying, deployed, demonstrable agent fleet to the All Things Agentic Hackathon by 2026-08-31 17:00 PT — one that RelayOps can run as its production reactivation product the day after, win or lose.**

Every decision in this repo is judged against that sentence. Work that improves the demo but leaves nothing behind for the business is suspect; work that improves the business but cannot be shown in four minutes is deferred, not dropped.

## 2. What this project is

A multi-tenant background agent fleet for med-spa client reactivation. A clinic's booking-software export goes in; approved, CASL-compliant win-back drafts and a defensible invoice come out.

Nightly, per tenant: Cloud Scheduler → a publisher job → one Pub/Sub message per lapsed client → a Cloud Run worker running an ADK agent pipeline (gates → segment → outreach) → Cloud SQL, with every model call logged. A human approves every message before it goes anywhere.

## 3. Business context that shapes decisions

- **The operator** is Sean R. (Toronto), running RelayOps solo: done-for-you client win-back for med spas and aesthetic clinics in the GTA.
- **Pricing**: $50 per client who books and shows, **each client counted once**, capped at $1,500 (the "14-Day Recovery Sprint" pilot). Continuity offer: "Retention Engine", $997/mo. The nightly run in this repo *is* the Retention Engine.
- **The agent's output becomes an invoice.** That is the bar. A wrong summary is embarrassing; a wrong bill is a dispute with a paying clinic.
- **CASL and PIPEDA are binding, not aspirational.** Canadian commercial electronic messages must identify the sender and carry working unsubscribe. Consent status is logged per client from first contact.
- **Positioning is outcomes, not AI.** The clinic buys recovered revenue. "AI employee" language stays out of client-facing copy — including anything this system generates.
- **Track separation is a hard inherited rule.** RelayOps holds two datasets: Track 1 (prospect *businesses* it sells to) and Track 2 (a signed clinic's *customers* — consumer PII). **They never join.** This repo is Track 2 only.

## 4. Why this is different from the other 3,000 entries

Stated here because it shapes what gets built, not just how it is pitched.

| Most entries | This one |
|---|---|
| Agent autonomy is the headline | The **forbidden set** is the headline — the agent cannot decide contact eligibility, cannot do arithmetic, cannot send, cannot act unlogged |
| Synthetic problem | A live business with a live bottleneck and a published price |
| Model does the reasoning end to end | Model interprets; Python decides. Gates, percentiles and billing are pure functions with unit tests |
| Compliance is a slide | CASL enforcement is executable code that runs after every generation |
| Output is text | Output is a **billable event**, recomputed from an append-only log and defensible line by line |
| Prompt injection is theoretical | The threat is real and specific: a clinic's free-text `notes` column is attacker-adjacent input that reaches a prompt. Model Armor has an actual job |
| Demo shows the happy path | Demo shows the **refusals** — a client gated with zero token spend, a VIP draft flagged for discount language, an injected instruction caught |

The last row is the one to build the video around. Nobody else will demo their agent declining to act.

## 5. Current state (2026-08-16)

Scaffold plus a completed qualification spike (F-1 steps 1–4). Nothing committed yet.

**Proven:** GCP project `relayops-fleet` provisioned and billed with 10 APIs enabled; Gemini ≥3.5 reachable and returning strictly-valid `SegmentDecision`; an ADK agent carrying the real production shape (strict `output_schema` + `before_agent_callback` injecting Python-computed facts) **deployed to Cloud Run and verified end to end** — HTTP 200, exact schema match, injected facts cited, VIP correctly not discounted. `pytest` collects (14 skipped, all unimplemented), `ruff` clean.

**Settled configuration** (corrected from the scaffold's assumptions — see [docs/F1-qualification-evidence.md](docs/F1-qualification-evidence.md)):

| | |
|---|---|
| Vertex location | `global` — Gemini ≥3.5 is **not** served regionally |
| Segment model | `gemini-3.7-flash` (no Pro-tier ≥3.5 exists) |
| Outreach model | `gemini-3.5-flash` |
| Cloud Run region | `us-central1` (independent of the Vertex location) |

**Not built:** everything under `core/`, the Pub/Sub fabric, Cloud SQL, the dashboard. Agent Engine (F-1 step 5) is the outstanding decision for GAP-012.

## 6. Goals

| # | Goal | Measure |
|---|---|---|
| G1 | Submission qualifies | Gemini 3.5+ on Vertex ✕ ADK ✕ ≥2 GCP services, all demonstrable in the video |
| G2 | The fleet runs unattended | One Cloud Scheduler trigger produces drafts for every eligible client across every tenant with no human in the loop until approval |
| G3 | No client is contacted who should not be | 100% of gate exclusions carry a recorded reason; zero model calls on gated clients |
| G4 | Every draft is explainable | Every draft in the dashboard links to the `agent_decisions` row that produced it |
| G5 | Reproducible by a judge | `deploy/deploy.sh` provisions from an empty GCP project; README spin-up works on a clean machine |
| G6 | Usable by RelayOps on Sept 1 | A real clinic can be onboarded as a tenant without code changes |
| G7 | No PII leaves the boundary | Zero real client or prospect records in the repo, the video, or any log |

## 7. Tech stack + commands

| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | Matches `relayops-prod`, so ported code needs no rewriting |
| Model | Gemini 3.5 Pro / Flash via Vertex AI | `google-genai>=2.0`; **availability verified day 1 (GAP-001)** |
| Agent framework | Google ADK | Hackathon-required; replaces LangGraph from `relayops-prod` |
| Compute | Cloud Run services + jobs | Worker, dashboard, publisher |
| Async | Pub/Sub + DLQ | One message per client |
| State | Cloud SQL Postgres + Alembic | Migrations 0001–0006 |
| Safety | Model Armor | On CSV-derived free text only |
| UI | FastAPI + Jinja2 | Server-rendered, no JS build step |

```bash
pip install -e ".[dev,dashboard]"
pytest
ruff check .
uvicorn relayops_fleet.dashboard.app:app --port 8501
PROJECT_ID=... ./deploy/deploy.sh
```

## 8. Repo layout

```
src/relayops_fleet/
  config.py          frozen Settings from .env
  schemas.py         strict pydantic: SegmentDecision, OutreachDraftSet, CampaignRunMessage
  core/              DETERMINISTIC ONLY — no LLM call may appear in this package
    gates.py         consent/opt-out/suppression/cooldown  (F-4)
    features.py      lapse buckets, per-clinic VIP percentile (F-7)
    casl.py          STOP line, unsubscribe, VIP-discount + overclaim flags (F-5)
    importer.py      Jane/Fresha/Vagaro/Mindbody/Boulevard column synonyms (F-3)
    attribution.py   computed billing from the outcome log (F-11)
  agents/            ADK agents + callbacks (F-7, F-9)
  fabric/            publisher (fan-out) + worker (push handler) (F-6)
  db/                models, repo — every query clinic-scoped (F-2)
  obs/decisions.py   agent_decisions, mandatory before output is used (F-10)
  dashboard/app.py   approval surface, no model access (F-8)
deploy/deploy.sh     provision + deploy from empty project (F-12)
```

## 9. Disclosed prior work

Hackathon rules require projects be newly created during the submission period, with pre-existing code disclosed.

**Ported from the author's private `relayops-prod`** (deterministic, non-agentic domain logic): CSV column-synonym mapping, E.164 + consent gates, CASL copy enforcement, attribution math, the strict pydantic output models, and the Alembic migration approach.

**New work for this hackathon**: the entire ADK implementation (replacing LangGraph), the Pub/Sub fan-out fabric, the multi-tenant schema and isolation tests, Model Armor integration, the approval dashboard's decision-log surface, and all Google Cloud provisioning and deployment.

This disclosure is reproduced in README.md and must appear in the Devpost submission text.

## 10. Milestones

Each is independently shippable; if the clock runs out mid-list, everything before the cut is still a working system.

| M | Milestone | Closes | Definition |
|---|---|---|---|
| **M1** | **Qualification proven** | GAP-001, GAP-002 | Gemini 3.5 reachable on Vertex in-region; a hello-world ADK agent runs on Cloud Run; Cloud SQL up with migrations applied. *If M1 fails, the whole plan changes — so it is day 1–2, not day 10.* |
| **M2** | Deterministic core green | GAP-003, GAP-004 | `core/` ported, `pytest` passes with no network and no model |
| **M3** | Agents produce drafts | GAP-005 | Segment + outreach ADK agents produce validated, CASL-enforced drafts for one client, locally |
| **M4** | The fleet runs itself | GAP-006 | Scheduler → publisher → Pub/Sub → worker → Cloud SQL, unattended, idempotent, with DLQ |
| **M5** | The human gate exists | GAP-007 | Approval dashboard live on Cloud Run with the decision log and the skipped-clients view |
| **M6** | Governance layer | GAP-008 | Model Armor on untrusted fields; per-role service accounts; Memory Bank per clinic |
| **M7** | Judgeable | GAP-009, GAP-010 | `deploy.sh` works from an empty project; architecture diagram; 4-min video; Devpost text with disclosure |

## 11. Definition of done

- `pytest` passes; `ruff check .` clean.
- No LLM import or call exists anywhere under `src/relayops_fleet/core/`.
- No query against a tenant table lacks a `clinic_id` predicate.
- No real PII in the repo, the fixtures, the video, or any committed log.
- Every `F-x` claimed complete has its `GAP-xxx` marked `CLOSED (<commit>)` in the same commit as the fix.
- `deploy/deploy.sh` runs to completion against a fresh GCP project.
