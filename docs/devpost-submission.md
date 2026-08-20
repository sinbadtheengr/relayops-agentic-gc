# Devpost submission text

Paste-ready. Filed under **The Fortified Enterprise Fleet**. The prize-category question is still the operator's — see **Before submitting** at the end.

---

## Elevator pitch (200 chars)

> A multi-tenant background agent fleet that turns a med spa's lapsed-client export into approved, CASL-compliant win-back campaigns — and into an invoice it can defend.

---

## Inspiration

I run RelayOps, a small business doing done-for-you client win-back for med spas in the Greater Toronto Area. I charge $50 per client who books and shows up, capped at $1,500.

That pricing is the whole reason this project is shaped the way it is. **The output of these agents becomes an invoice someone is going to argue with.** A wrong summary is embarrassing. A wrong bill is a dispute with a paying customer. And because the messages go to real people under Canada's Anti-Spam Legislation, a mistake isn't a bad demo — it's a liability.

So I did not build an agent that does as much as possible. I built one with a carefully chosen set of things it is forbidden to do.

## What it does

Every night, for every clinic, the fleet fans out **one Pub/Sub message per lapsed client**. Each message runs a small pipeline on Cloud Run:

1. **Compliance gates** — pure Python. Opt-out register, clinic suppression list, 14-day cooldown, phone validation. A gated client stops here and **costs zero tokens**.
2. **Feature computation** — pure Python. Lapse buckets, and VIP status as the 80th percentile of spend *within that clinic*.
3. **Segment agent** — ADK + Gemini 3.7 Flash. Decides whether the client is worth contacting, which priority tier, and which approved campaign template fits.
4. **Outreach agent** — ADK + Gemini 3.5 Flash. Adapts the clinic's signed-off template into SMS and email copy.
5. **CASL guards** — pure Python. Guarantees the STOP line and unsubscribe footer, and flags discount language in a VIP draft.
6. **A human.** Drafts land in an approval dashboard. Nothing is sent by a machine.

Between runs, the fleet remembers. Once outcomes are recorded, what actually converted at a clinic — which approved template section, which channel, how many of those contacted came back — is recomputed from the outcome log and stored in that clinic's **Agent Engine Memory Bank** scope, then read back into the next run's prompts.

## What the agents are forbidden to do

This is the design, not a disclaimer:

- **They cannot decide who may be contacted.** Consent, opt-out, suppression and cooldown are pure functions that run *before* any model call.
- **They cannot do arithmetic.** Lapse buckets, VIP percentiles and billing are computed in Python and handed to the model as authoritative facts.
- **They cannot send.** There is no send path in the repository.
- **They cannot act unlogged.** Every model call — and every rule-based refusal — writes an `agent_decisions` row in the same transaction as the work it explains.

- **They cannot write their own memory.** Campaign memory is composed in Python from enumerated values and integers. A memory is written once and injected into every later prompt, so model-authored text in one would be a stored prompt injection with an indefinite blast radius.

Opt-outs are stored **globally** while cooldowns are stored **per clinic**. That asymmetry is deliberate: under-suppressing is a compliance risk, over-suppressing only costs a lead.

Memory is scoped the same way, and for a blunter reason: cross-tenant memory is a data leak wearing a feature's clothes. Every read and write carries the clinic's scope, retrieval matches it exactly, and the facts are aggregates that name no individual.

## How I built it

**Gemini 3.7 Flash** (segment) and **Gemini 3.5 Flash** (outreach) on **Vertex AI**, both with strict pydantic `response_schema` so any drift fails loudly. **Google ADK** for both agents. **Cloud Run** services for the worker and dashboard, a **Cloud Run Job** for the fan-out, **Pub/Sub** with dead-lettering, **Cloud SQL** (Postgres 16), **Cloud Scheduler**, **Secret Manager**, **Cloud Logging**. One `deploy.sh` provisions all of it.

**Agent Engine** hosts the per-clinic Memory Bank, on a bare instance with no agent code deployed to it — Memory Bank is a property of the resource, not of an agent running on it.

**Multi-tenancy is enforced, not promised.** A runtime guard rejects any SQL statement touching tenant data without a `clinic_id` predicate, before it reaches Postgres — cruder than row-level security and deliberately louder, because it fails on the developer who wrote the query rather than silently in production.

## Challenges I ran into

**`gemini-3.5-pro` does not exist.** I planned a Pro-for-judgement / Flash-for-copy split. There is no Pro-tier model at ≥3.5 on any endpoint I can reach, so both tiers are Flash. The newest model was also the fastest and the cheapest in tokens.

**Gemini ≥3.5 is served only on the `global` endpoint.** The models appear in `models.list()` for `us-central1` and then return 404 on `generate_content` there. That produced a standing rule for this project: *a model appearing in the catalogue is not proof it is callable.*

**ADK interpolates `{var}` in instructions from session state.** My approved campaign templates are full of `{{merge_field}}` placeholders, so ADK read the inner `{clinic_name}` as a state variable and raised `KeyError`. Facts and templates now travel in the user message, which ADK does not template — which is a better design anyway.

**My own tenant guard caught two of my own bugs.** First, I stored its bypass flag on SQLAlchemy's `Connection.info`, which lives on the *pooled* connection — so one legitimate bypass left that connection permanently unguarded for every later checkout. The guard silently stopped guarding, which is worse than never having had one. Unit tests could not see it; the first run against a real pool did. Second, mutating a draft's status via the ORM emits `UPDATE ... WHERE id = X` with no `clinic_id`, which the guard rightly rejected.

**I shipped an image without the approved campaign copy.** My `.gcloudignore` excluded `*.md` as documentation, silently dropping `templates/campaign-templates.md`. The container built, started, and passed health checks — then failed at draft time, *after* the model call had already been paid for. The Dockerfile now asserts that file exists at build time. A build that cannot produce a draft should never reach a registry.

## Accomplishments I'm proud of

The **skipped-clients view**. It lists everyone the system deliberately did *not* contact and why, alongside a token count of zero. The drafts queue shows who was contacted; the compliance question is who wasn't.

And the fact that **guard false-positives were treated as bugs**. `\bdiscount\b` matches inside "non-discount"; `\bincentive\b` matches inside "no incentive". Both are compliant phrases, and both are exactly what Gemini 3.7 and 3.6 actually produced. A guard that flags correct copy is worse than no guard — reviewers learn to click past the badge, and then miss the real one.

## What I learned

Most of what went wrong was invisible locally and only appeared once deployed: the global endpoint, the missing template file, `/healthz` being intercepted by Cloud Run, Cloud Run answering 404 rather than 403 for an unauthorized caller. Every one of those is now a commented step in `deploy.sh`, written down the moment it was solved rather than reconstructed later.

The broader lesson: **the constraints made the product, not just the compliance story.** Forcing every number into Python made the agents cheaper, faster and testable. Forcing every refusal into a logged row produced the most persuasive screen in the demo.

## What's next

Model Armor on the clinic-supplied free-text `notes` column — a genuine injection surface, since that text reaches a prompt. Attribution and the invoice view. Then onboarding a real clinic, which is what this was for.

---

## Technologies used

`Google ADK` · `Gemini 3.7 Flash` · `Gemini 3.5 Flash` · `Vertex AI` · `Vertex AI Agent Engine` · `Agent Engine Memory Bank` · `Model Armor` · `Cloud Run` · `Cloud Run Jobs` · `Cloud SQL (Postgres 16)` · `Pub/Sub` · `Cloud Scheduler` · `Secret Manager` · `Cloud Logging` · `Artifact Registry` · `Cloud Build` · `Python 3.12` · `FastAPI` · `SQLAlchemy` · `Alembic` · `pydantic` · `Jinja2`

## Data sources

**Synthetic only.** The demo tenant in `scripts/seed_demo_tenant.py` is entirely generated — every name, phone number and spend figure. No real clinic's client list appears in this repository, its deployment, or the demo video.

In production the input is a clinic's own export from Jane, Fresha, Vagaro, Mindbody or Boulevard.

## Prior work disclosure

Per the rules, this project was newly created during the submission period (August 2026).

It reuses **deterministic, non-agentic domain logic** ported from my own private `relayops-prod` repository: consent/E.164 gating, CASL copy enforcement, campaign templates, and the strict pydantic output models.

**New work for this hackathon:** the entire ADK implementation (replacing LangGraph, which is not an accepted framework), the Pub/Sub fan-out fabric, the multi-tenant schema and its runtime isolation guard, the approval dashboard, the decision log, and all Google Cloud provisioning and deployment.

---

## Before submitting — decisions still open

1. **Track — settled: The Fortified Enterprise Fleet.** All three named components are built: Agent Runtime (the ADK agents), Memory Bank (per-clinic campaign memory), and Agent Identity (three least-privilege service accounts, with the approval surface deliberately denied `aiplatform.user`).
2. **Startup Excellence** ($20k) requires an incorporated organisation and a corporate email address. The Devpost registration currently uses a personal Gmail, and a RelayOps-branded Gmail does not qualify.
3. **Optional bonus:** a public blog post or a `#AllThingsAgenticHackathon` social post. The "guard false-positives are bugs" story is the one worth writing up.
