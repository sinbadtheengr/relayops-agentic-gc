# All Things Agentic Hackathon — RelayOps submission proposal

> Drafted 2026-08-15. Deadline **2026-08-31 17:00 PT** — 16 days.
> Companion docs to follow once approved: `PROJECT_OVERVIEW.md`, `CLAUDE.md`, `GAPS_AND_ISSUES.md`.

---

## 1. What the hackathon actually rewards

| Requirement | Verdict for RelayOps |
|---|---|
| Gemini 3.5+ via Gemini API or Vertex AI | Already on `google-genai>=2.0` + Vertex in `relayops-prod`. Bump the model, keep the client. |
| Agent framework: **ADK**, GenAI SDK, Antigravity, GenKit | You shipped an ADK `SequentialAgent` in `relayops-agentic-cine`. Reuse that muscle. LangGraph is *not* on the list — the port is the real work. |
| GCP infra: Cloud Run, Cloud SQL, Firestore, GKE, Pub/Sub | Currently docker-compose Postgres + local CLI. This is the genuine gap and the genuine business upgrade. |
| Theme: agents that "run in the background, handle massive datasets, automate asynchronously" | A nightly fan-out over every lapsed client of every clinic is exactly this shape. |

**Judging weights:** Innovation & Operational Utility 40% · Architectural Discipline 30% · Demo & Production Readiness 30%.

**The rule that constrains everything:** projects must be *"newly created during the Submission Period"* (Aug 3–31), with pre-existing code disclosed. So this is a **new repo** (`relayops-agentic-gc`) that imports the deterministic RelayOps core as a disclosed, vendored library — not a re-upload of `relayops-prod`. That is allowed and normal; it also happens to be the right engineering call.

---

## 2. The proposal: RelayOps Revenue Recovery Fleet

**A multi-tenant background agent fleet that turns a med spa's booking-software export into approved, CASL-compliant win-back campaigns — and into a defensible invoice.**

Every night, per tenant clinic, the fleet fans out one agent run per lapsed client: gate → segment → draft → queue for human approval. Nothing is ever sent by a machine. Every LLM decision is logged with its inputs, outputs, model, tokens, and reasoning. Billing is recomputed from an append-only outcome log, never stored as a status flag.

### Why this one and not the prospecting pipeline

You have two candidate systems. Track 1 (Google Places → audit → cold pitch, selling RelayOps) is your *live* bottleneck — 1,426 prospects, 391 pitch-ready, **0 contacted**. Track 2 (client reactivation) is the product you charge $997/mo for.

Submit **Track 2**, for three reasons:

1. **The demo video is public.** A 4-minute video naming real GTA med spas and announcing "booking page is broken / website is down" is a reputational and privacy liability you cannot walk back. Track 2 demos on synthetic clinic data — you already have a 100-customer demo dataset in `relayops-ai-rebooking-agent`.
2. **Multi-tenancy is the architecture story.** Per-clinic scoping, globally-scoped opt-outs, and the hard "never join Track 1 and Track 2 datasets" rule are real governance most weekend entries won't have. That's the 30% architecture score.
3. **It's the sellable asset.** A deployed multi-tenant service is what lets you onboard clinic #1 without your laptop being the production runtime.

Track 1 stays exactly where it is. It works. Don't touch it during a 16-day sprint.

### Agent fleet

| Stage | Type | Source | Work needed |
|---|---|---|---|
| **Intake** | ADK agent + deterministic parser | `pipeline/client_import.py` — Jane/Fresha/Vagaro/Mindbody/Boulevard column-synonym mapping, skip-with-reason | Port; trigger from GCS upload |
| **Gate** | Pure Python, **no LLM** | E.164 validation, `opt_outs`, suppression list, 14-day cooldown, per-clinic scoping | Port as-is |
| **Segment** | ADK + Gemini 3.5 structured output | `pipeline/segment_agent.py` — LangGraph today | **Rewrite as ADK**; keep features deterministic |
| **Outreach** | ADK + Gemini | `pipeline/outreach.py` — `enforce_casl()`, overclaim flagging | Port; guards stay rule-based |
| **Approval** | FastAPI on Cloud Run | `relayops.dashboard` | Port; add decision-log viewer |
| **Attribution** | Pure Python | `relayops.attribution` — computed, never stored | Port |

The pattern you already proved in ReelRelay holds: **the model interprets, Python decides.** A `before_agent_callback` hands the segment agent finished features; compliance gates are rule-based and the model can never override them.

### Async fabric (the new part)

```
Cloud Scheduler (nightly, per tenant)
  └─> Pub/Sub topic  relayops.campaign.run
        └─> Cloud Run Job  (fan-out: one message per lapsed client)
              └─> ADK agent run  ──> Cloud SQL (Postgres, Alembic migrations)
                                 └─> agent_decisions + Cloud Logging
Cloud Run Service  approval dashboard  ──> reads/writes the same Cloud SQL
```

Dead-letter topic on the fan-out; idempotent writes on `(clinic, client_key, channel)` so a redelivery can't double-draft. Per-client cost cap via the existing `*_MAX_*` env convention.

### Governance layer (this is what wins architecture points)

- **Model Armor** on every field derived from an uploaded CSV. This is not a checkbox — a clinic's free-text `notes` column is untrusted input that reaches a Gemini prompt. Prompt injection via customer notes is a real threat in this design.
- **Vertex AI Agent Engine Memory Bank** for per-clinic campaign memory: which tone and which offers actually converted, carried into the next wave.
- **Per-tenant agent identity** so a clinic's agent run cannot read another clinic's rows — enforced at the service-account level, not just the WHERE clause.
- **Decision log** (`agent_decisions`) as a first-class product surface, not a debug table: every draft in the approval UI links to the exact model call that produced it.

---

## 3. Two decisions to make before day 1

### Decision A — which hackathon track

| Track | Fit | Field size |
|---|---|---|
| **The Taskmaster** | Strong. Multi-step workflow taking concrete action: ingest → gate → segment → draft → queue → bill. | Crowded — most entries land here. |
| **The Fortified Enterprise Fleet** | Also strong, and its named components (Agent Registry, Runtime, Memory Bank, Identity, Gateway, Model Armor) map onto Vertex AI Agent Engine. Your multi-tenant PII isolation is a genuine fleet-governance story. | Thin — most hobbyists won't touch Agent Engine. |

**Recommendation:** build the codebase so the two are the same product — only the deployment target and the framing differ. Spend days 1–3 attempting **Agent Engine** deployment. If it lands cleanly, file under **Fleet** (less crowded, $20k). If it fights you, fall back to Cloud Run + **Taskmaster**. Either way you stay eligible for **Best Architectural Design** ($5k), which is the realistic second shot.

### Decision B — Startup Excellence eligibility

$20,000, and it requires *"an organization which must be incorporated"* plus *"your corporate email address."* You're currently registered under `sean.relleve@gmail.com`.

If RelayOps is incorporated, set up a corporate mailbox on the RelayOps domain **this week** and file under Startup Excellence — the field there is a fraction of the open tracks. If it isn't, you're in Individual/Hobbyist ($10k × 2), which is also a smaller field than the main tracks. Confirm which before writing the Devpost page.

---

## 4. 16-day plan

| Days | Work |
|---|---|
| 1–2 | GCP project, Vertex enabled, Cloud SQL instance, Alembic migrations applied against it. Repo skeleton + pre-existing-code disclosure. Hello-world ADK agent on Gemini 3.5. **Verify model availability in your region on day 1.** Attempt Agent Engine (Decision A). |
| 3–5 | Port the deterministic core into `relayops_core` — import, gates, scoring, `enforce_casl()`, attribution. Zero LLM. Test suite green against Cloud SQL. |
| 6–8 | ADK agents: intake, segment, outreach. `before_agent_callback` supplies computed facts. Structured output validated against known option sets. |
| 9–10 | Async fabric: Cloud Scheduler → Pub/Sub → Cloud Run Job fan-out, DLQ, idempotency. |
| 11–12 | Approval dashboard on Cloud Run + decision-log viewer. Auth (IAP or the existing `DASHBOARD_PASSWORD`). |
| 13 | Model Armor on CSV-derived fields; Memory Bank per clinic. |
| 14 | Reproducibility: `deploy.sh` or Terraform, README spin-up, seeded synthetic dataset. This is 30% of the score — do not leave it to day 16. |
| 15 | Architecture diagram, 4-minute video (problem → value → live run → GCP console proof), Devpost writeup. |
| 16 | Buffer. |

**Explicitly out of scope:** the audit engine, PageSpeed, CrUX, headless crawling, the Track-1 prospect pipeline, Slack surfaces. All of it works already elsewhere; none of it is load-bearing for this demo.

---

## 5. What you keep if you don't win

This is the part that justifies the 16 days on its own.

1. **Your $997/mo product becomes deployable.** The "Retention Engine" continuity offer *is* a nightly background run. Right now that runtime is your laptop. After this, it's Cloud Run — you can onboard clinic #1 without being the infrastructure.
2. **The security work gets forced.** Secret management, IAM, per-tenant isolation, no-PII-in-repo. That's the actual blocker to a clinic owner handing you a client list, and it's work you'd otherwise keep deferring.
3. **Billing becomes defensible.** Attribution recomputed from an append-only log on managed Postgres is the invoice you send — and the one you can defend line-by-line when a clinic argues.
4. **The demo video is sales collateral.** A 4-minute "here's exactly what happens to your lapsed client list" explainer for the homepage, and an attachment for the 391 pitch-ready prospects sitting uncontacted in your outreach queue.
5. **The Devpost page is credibility.** A public Google Cloud hackathon entry is a real asset on a solo-operator sales call with a clinic that's deciding whether you're a business or a guy.
6. **Credits cover hosting.** $150 now, $1k–5k if you place — the first few clinics run free.

---

## 6. Risks

| Risk | Mitigation |
|---|---|
| "Newly created during Submission Period" | New repo, disclosed reuse of the deterministic core. Never re-upload `relayops-prod`. |
| Real PII in a public repo or video | Synthetic clinic data only. The `data/`-is-radioactive rule from `relayops-internal` carries over verbatim. |
| Gemini 3.5 regional availability | Verify day 1, before anything is built on it. |
| Fan-out cost blowup | Reuse the `*_MAX_*` cap convention; dry-run fixtures by default. |
| Scope creep into the audit engine | It's listed as out of scope above. It stays out of scope. |
| Solo, 16 days, day job | ~70% of the domain logic already exists and is tested. The new surface is deployment and orchestration, not product. |
