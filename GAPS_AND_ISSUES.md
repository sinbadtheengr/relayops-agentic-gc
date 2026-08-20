# GAPS_AND_ISSUES — RelayOps Revenue Recovery Fleet

**Verified against:** `9320dc1` (2026-08-16).

> **Normal contract now in force.** This file began as a register of unbuilt
> work, because at scaffold time there was no code to verify defects against.
> Code now exists, so the standard rule applies from here: **no gap may be
> listed that was not verified against real code at a named commit.** New
> entries carry `file:line` links and the commit they were observed at.
>
> Entries still marked OPEN below describe work specified but not yet
> implemented — they are labelled as such, and each names the stub file that
> currently stands in for it.

**Severity ladder** — defined by impact on the single job (*ship a qualifying, deployed fleet by Aug 31 that RelayOps can run on Sept 1*):

| Severity | Meaning |
|---|---|
| **Critical** | Disqualifies the submission, or would leak PII / contact someone unlawfully |
| **High** | Submission is judged badly, or the business cannot use the result |
| **Medium** | Costs demo quality or operator time |
| **Low** | Polish |

---

## Critical

### GAP-001 — Gemini 3.5 availability on Vertex is unverified
**Category:** qualification · **Status:** **CLOSED (89aa3ec)** · **Spec:** [F-1](CLAUDE.md#f-1)
**Evidence:** [docs/F1-qualification-evidence.md](docs/F1-qualification-evidence.md) · **Reproduce:** `PYTHONPATH=src python scripts/spike_f1.py`

The assumption was wrong in two ways, both caught before anything was built on it:

1. **`gemini-3.5-pro` does not exist.** No Pro-tier model at ≥3.5 is available on any accessible endpoint. Both agents now use flash-tier models.
2. **≥3.5 models are global-endpoint only.** They appear in `models.list()` for `us-central1` but return 404 on `generate_content` there. `gemini-2.5-*` works regionally, which is why `relayops-prod` never hit this.

Settled and written into `.env.example` + `config.py`: `GOOGLE_CLOUD_LOCATION=global`, segment `gemini-3.7-flash`, outreach `gemini-3.5-flash`. All three candidates returned strictly-valid `SegmentDecision` output; the newest was also fastest and cheapest.

**Standing rule this produced:** a model appearing in `models.list()` is not proof it is callable. Any model change is verified with a real call.

### GAP-002 — No Google Cloud project, no deployed anything
**Category:** qualification · **Status:** **CLOSED (f4ac6dc)** · **Spec:** [F-1](CLAUDE.md#f-1), [F-2](CLAUDE.md#f-2), [F-12](CLAUDE.md#f-12)
**Evidence:** [docs/F1-qualification-evidence.md](docs/F1-qualification-evidence.md)

Done: project `relayops-fleet` created and billed; 10 APIs enabled; an ADK agent carrying the real F-7 shape (strict `output_schema` + `before_agent_callback`) **deployed to Cloud Run and verified end to end** — HTTP 200, exact schema match, injected facts cited, VIP not discounted. Service is `--no-allow-unauthenticated`.

Everything this gap listed as outstanding is now live: Cloud SQL Postgres 16, Pub/Sub with a dead-letter topic and push subscription, Cloud Scheduler, and the three real services (worker, dashboard, publisher job) on their own least-privilege service accounts. The F-1 spike service was deleted once the real worker replaced it.

The predicted risk was accurate — Cloud SQL connectivity and IAM did consume the most time, and produced most of the deployment findings recorded in F-12.

### GAP-003 — Compliance gates do not exist in this repo
**Category:** compliance · **Status:** **CLOSED (bb89641)** · **Spec:** [F-4](CLAUDE.md#f-4)

`core/gates.py` is implemented as pure functions over pre-loaded values — no database, no clock, no network — so the compliance boundary is exhaustively testable. Gate order is fixed: `invalid_phone → opted_out → suppressed → cooldown → no_last_visit`, with the most serious applicable reason recorded (an opt-out never expires; a cooldown does, and logging the lesser reason would misrepresent the exclusion).

Loaders live in `db/consent_repo.py`, outside `core/`: opt-outs read **globally** (through the explicit `unguarded()` marker), cooldown reads **per clinic**. 34 tests — 27 needing no infrastructure, 7 against real Postgres, including the `relayops-prod` cross-clinic cooldown bug asserted through the real loader.

**Two schema gaps this port exposed, both closed by migration `0002`:**

1. `clients` had no `email` column, while `outreach_drafts` accepted an `email` channel — every email draft would have been undeliverable.
2. `opt_outs.client_key` was `NOT NULL`, so an **email unsubscribe could not be recorded at all**. Under CASL the unsubscribe mechanism must actually work; silently discarding one is the failure mode carrying real liability. `client_key` is now nullable with a sibling `email`, a CHECK that at least one identifier is present, and partial unique indexes.

The `0002` downgrade deliberately **refuses** to run while email-only opt-outs exist, rather than deleting suppression records to satisfy a schema rollback — dropping those would re-open contact to people who opted out.

### GAP-011 — No tenant isolation enforcement
**Category:** data integrity · **Status:** **CLOSED (063c801)** · **Spec:** [F-2](CLAUDE.md#f-2)
**Verified against Cloud SQL:** migration `0001` applied; 9 tables, 58 CHECK constraints live; 33 tests green including 9 integration tests.

Multi-tenancy is the architectural claim of the submission. In `relayops-prod` this exact bug was real: `client_decisions.client_key` was globally unique on the phone number, so two clinics sharing a customer silently overwrote each other's decision, and one clinic's outreach put the other's same-phone customer into cooldown.

Now enforced in three layers rather than by convention:

1. **Schema** — `clinic_id NOT NULL` on all six tenant tables; `UNIQUE (clinic_id, client_key)`; `opt_outs` deliberately global.
2. **Runtime** — `db/repo.py::install_tenant_guard` rejects any SELECT/UPDATE/DELETE touching a tenant table without a `clinic_id` predicate, before it reaches Postgres. `unguarded()` is the single, explicit, greppable escape hatch.
3. **Tests** — 24 checks in `tests/test_tenant_isolation.py`, no database and no network required, so they cannot become slow enough to disable.

`get_clinic()` raises rather than creating, so a typo cannot silently split one clinic across two tenants.

**One real bug found by doing this against a live database.** The bypass was first stored on SQLAlchemy's `Connection.info`, which is kept on the *pooled connection record* and survives check-in. A single `unguarded()` call therefore left that connection permanently unguarded, and every later checkout of it ran unscoped — the guard silently stopped guarding, which is worse than never having had one. Unit tests could not see it; the first integration run against a real pool did. The bypass is now a `ContextVar` that resets with its token, and `test_bypass_does_not_survive_connection_reuse` pins the behaviour.

---

## High

### GAP-004 — Deterministic core not ported
**Category:** implementation · **Status:** **CLOSED (6a9ee7b)** · **Spec:** [F-3](CLAUDE.md#f-3), [F-5](CLAUDE.md#f-5), [F-7](CLAUDE.md#f-7), [F-11](CLAUDE.md#f-11)

**Done:** `core/casl.py` (F-5) — CASL repair plus the VIP-discount and overclaim guards. `core/features.py` + `core/templates.py` (F-7) — lapse buckets, per-clinic VIP cutoff, approved-template selection.
**Also done:** `core/attribution.py` + `db/billing_repo.py` (F-11) — computed billing with 17 tests, plus the `/clinics/{id}/invoice` route F-8 deferred here.
**Also done:** `core/importer.py` (F-3) — synonym header matching across Jane/Boulevard/Vagaro/Mindbody/Fresha, column-level slashed-date disambiguation, skip-with-a-reason, and `scripts/import_clinic_export.py`. 42 tests. **The deterministic core is now complete.**

Verified against a realistic Jane-style export: `25/12/2025` settled the whole column to d/m/y with no ambiguity warning; a row with an unreadable phone and a row with no date were each skipped by name and line number; a surname-only row still imported, because a client with only a surname still has a name.

**One rule F-11 had to settle that the source could not.** `outreach_outcomes.occurred_on` is a date while `contact_log.contacted_at` is a timestamp, so same-day ordering is unknowable from the data. Same-day contact **counts as prior**: texted in the morning and came in the afternoon is the best outcome this product produces, and excluding it would systematically under-bill the campaigns that worked best. The rule is stated in the docstring and pinned by a test rather than silently resolved.

The first show is the billable one, not the latest — otherwise a correction to an early appointment would silently move the charge.

**Nothing remains.** Every module under `core/` is implemented and tested.
**Fix:** M2, and port the tests with the code.

**Calibration note carried forward.** The inherited guard regex was mis-tuned for the models actually in use: `\bdiscount\b` matches inside *"non-discount"* and `\bincentive\b` inside *"no incentive"* — both compliant phrases that `gemini-3.7-flash` and `gemini-3.6-flash` produced during F-1. Porting it unchanged would have badged the phrasing the current models favour. Negated offers and hedged claims are now stripped before matching. A guard that flags correct copy is worse than no guard: reviewers learn to click past the badge and then miss the real one.

Also found while porting: `\b#\s?1\b` **could never match** — `#` is a non-word character, so `\b` never holds before it and that alternative was dead in the inherited expression.

### GAP-005 — LangGraph → ADK port not done
**Category:** qualification · **Status:** **CLOSED (ad95be5)** · **Spec:** [F-7](CLAUDE.md#f-7)

Segment and outreach are ADK `LlmAgent`s with strict `output_schema`, verified live against Vertex: the segment decision cites the injected `412000` spend verbatim, and the VIP draft passes every CASL guard with merge fields intact. `compute_features` was ported; LangGraph's `decide()` / `build_graph()` were discarded, which is precisely what ADK replaces.

**This closes the last qualification risk.** Gemini ≥3.5 ✅, ADK ✅, Cloud Run + Cloud SQL ✅.

**The trap worth remembering:** ADK interpolates `{var}` in *instructions* from session state, and the approved templates are full of `{{merge_field}}` placeholders — ADK reads the inner `{clinic_name}` as a state variable and raises `KeyError`. Facts and templates now travel in the user message, which ADK does not template. A test pins it.

Two smaller design decisions recorded in F-7: `load_template_section` **raises** on an unknown bucket where `relayops-prod` fell back to the whole document (which hands the model every segment's copy, discounts included, and invites it to pick); and the VIP cutoff returns 0 below five known spends rather than inventing a tier from a four-client book.

### GAP-006 — No async fabric
**Category:** implementation · **Status:** **CLOSED (78b7a8c)** · **Spec:** [F-6](CLAUDE.md#f-6)

Publisher job → Pub/Sub (`relayops.campaign.run`, topics created) → push worker, with an explicit DLQ and replay-safe writes. Verified end to end against Cloud SQL and live Gemini on the synthetic demo tenant:

```
published: 11
+14165550101 -> drafted        segment 1325 tok / 5.7s, outreach 3937 tok / 17.9s
+14165550109 -> gated:opted_out   rule, 0 tokens
+14165550110 -> gated:cooldown    rule, 0 tokens
```

That token column is the demo in one line: **refusals cost nothing.**

**Design decisions worth keeping:**
- **Gates run in the worker, not the publisher.** Filtering excluded clients at publish time would be cheaper and would leave no evidence; every exclusion now produces its own decision row.
- **The handler dead-letters poison messages itself, then acks.** Pub/Sub's native dead-lettering only fires after `max_delivery_attempts`, so a deterministically-broken message would be redelivered several times first — each retry spending tokens.
- **204 on permanent failure, 500 only on transient.** Returning 500 for a message that will fail identically builds an infinite retry loop that bills on every pass.
- **`upsert_draft` carries `WHERE status = 'draft'`.** A nightly re-run must never revise copy a human approved, revive a rejection, or reopen something sent.

**Found by running the suite after seeding the demo tenant:** the publisher tests assumed the database held only their own clinics, so any other tenant broke them. `publish_campaign_run` now takes `clinic_ids` — which an operator also wants, to re-run a single clinic without touching the others.

### GAP-007 — No human approval surface
**Category:** implementation · **Status:** **CLOSED (0f6d409)** · **Spec:** [F-8](CLAUDE.md#f-8)

FastAPI + Jinja2, server-rendered, no JS build step. Verified against the live seeded tenant: clinic picker, drafts queue tabbed by status, the decision view behind every draft, and the skipped-clients view. 13 integration tests.

**The two load-bearing behaviours, both tested as negatives:**
1. **Approve does not send** — it sets `status='approved'` and writes **no** `contact_log` row. A cooldown started for a message nobody sent would suppress a real future campaign. The button text says "Approve (does not send)", asserted by a test, because an operator who believes otherwise will eventually be very surprised.
2. **Mark sent writes `contact_log` BEFORE flipping the status**, in one transaction. A sent draft whose cooldown never started is how someone gets messaged twice. A test drives the full loop and confirms the next run gates that client on `cooldown`.

**Fails closed:** with no `DASHBOARD_PASSWORD` every route returns 503 rather than serving client PII openly.

**The tenant guard caught this feature's own code.** Mutating `draft.status` on a loaded ORM object makes SQLAlchemy emit `UPDATE outreach_drafts ... WHERE id = X` with no `clinic_id` — which would update another clinic's row given a wrong id. Status changes now go through `set_draft_status`, an explicit UPDATE naming both predicates. The guard has now paid for itself twice.

### GAP-014 — The qualifying models force a global endpoint, against a Canadian data-residency posture
**Category:** compliance · **Status:** **CLOSED (2d893d7)** · **Discovered:** 2026-08-15 by F-1 · **Spec:** [F-7](CLAUDE.md#f-7)

Every Gemini ≥3.5 model is served only from Vertex's `global` endpoint, which routes to whichever region has capacity. Anything in a prompt may therefore be processed outside Canada, while RelayOps sells to compliance-conscious GTA clinics and the marketing site makes explicit PIPEDA/CASL claims.

**Fixed by option (a): the agents are no longer given a direct identifier.** `first_name` was removed from `ClientFeatures` structurally — not merely filtered out of the prompt — so there is no field to reinstate by accident. What still leaves the process is lapse days, bucket, visit count, spend, VIP status and last service: those are *attributes*. A name is an *identifier*, and that distinction is exactly what a clinic owner is asking about when they ask where their client list goes.

The copy is still personal. The approved templates already carried `{{first_name}}` in 27 places, so the outreach agent now treats it as a merge field like `{{clinic_name}}`, and `core/personalize.py` substitutes the real name **locally, in-process, on the way to the database**. The clinic's own merge fields are deliberately left alone.

Verified live end to end: the saved draft reads `Hi Dana, it's {{staff_name}} at {{clinic_name}}…` while the prompt that produced it contained no name, no phone and no email.

The honest answer to "where do my clients' names go?" is now: they do not leave.

**Residual exposure, stated rather than glossed:**
- **Staff notes** may themselves contain personal detail. They are screened for injection (F-9) but not de-identified, because a note stripped of specifics is no longer worth including.
- **`agent_decisions.input` still stores the full state, including the name.** That is Cloud SQL in `us-central1` — our own database, not the model endpoint — so it is outside this gap, but it means the decision log is not a record of the literal prompt.
- **Cloud Logging** *(fixed 2026-08-16)* — entries carried `client_key`, an E.164 phone number, putting a direct identifier in a second sink with its own retention, access rules and export paths. It now logs `decision_id` instead, which joins back to the full row in Cloud SQL, so correlation is unchanged and the log identifies no one. A hash was rejected: a bare SHA of a 10-digit number is brute-forceable in seconds, and a keyed hash would add secret management for no extra benefit. `test_cloud_logging_carries_no_client_identifier` pins it.
- **The Pub/Sub dead-letter topic still carries the whole message, including `client_key`.** Deliberate and left as-is: a dead letter stripped of its client is not replayable, which defeats the point of having one. It is our own topic in our own project, with a bounded retention, rather than a broad ops sink.

### GAP-009 — No reproducible deployment path
**Category:** judging · **Status:** **CLOSED (f4ac6dc)** · **Spec:** [F-12](CLAUDE.md#f-12)

`deploy/deploy.sh` is complete and every step was performed by hand against `relayops-fleet` first. Live: three Cloud Run surfaces (worker service, dashboard service, publisher job), Pub/Sub push subscription with dead-lettering, three least-privilege service accounts, and a nightly Cloud Scheduler job at 09:00 America/Toronto.

**Verified end to end on real infrastructure**, publisher job → Pub/Sub → push worker → Cloud SQL: 4 clients fanned out, **7 drafts** produced across SMS and email, merge fields intact, 17,891 tokens, no draft flagged.

**Four deployment traps, all found by deploying rather than reading docs** (detail in [docs/F1-qualification-evidence.md](docs/F1-qualification-evidence.md)):

1. **The image shipped without the approved campaign copy.** `.gcloudignore` excluded `*.md` as documentation, silently dropping `templates/campaign-templates.md`. The container built, started and passed health checks — then failed at draft time, *after* the segment model call was paid for. The Dockerfile now asserts the file exists and contains `## Segment D`; a build that cannot produce a draft never reaches the registry.
2. **Cloud Run intercepts `/healthz`** and answers with its own HTML 404 before the request reaches the app. Health endpoints are `/health`.
3. **Cloud Run answers 404, not 403,** for an unauthorized caller — indistinguishable from a missing route. `--audiences` needs a service account, so a user account cannot mint a correctly-scoped token at all.
4. **FastAPI's `/docs` and `/openapi.json` were public** on the PII surface, served before the auth dependency. Disabled on the dashboard, with a test.

**Not yet verified:** the from-an-empty-project claim. Every step is idempotent and was run individually against a real project, but a second clean project has not been provisioned end to end — that costs another Cloud SQL instance. Flagged rather than assumed.

---

## Medium

### GAP-008 — No Model Armor, no per-role service accounts, no Memory Bank
**Category:** governance · **Status:** **CLOSED (9b46d8a)** · **Spec:** [F-9](CLAUDE.md#f-9)

**Per-role service accounts** (F-12): three, least privilege, with `relayops-dashboard` holding exactly `cloudsql.client` + `secretmanager.secretAccessor` and **no `aiplatform.user`** — the approval surface approves, it cannot generate.

**Screening of clinic-supplied free text**, in two layers:

1. `core/untrusted.py` — deterministic, offline, **always runs**.
2. Model Armor template `relayops-notes` (PI-and-jailbreak at `LOW_AND_ABOVE`, malicious-URI) — catches phrasings a regex will not, and **fails closed**: if it is configured but unreachable, the note is dropped rather than passed unscreened. A boundary that degrades to "allow" when a dependency is down is not a boundary.

Verified live end to end against real Gemini and real Model Armor:

| client | note | verdict | used | draft contains "90%" |
|---|---|---|---|---|
| Kai | the seeded injection | `blocked:override_attempt` | no | no |
| Marcus | "Prefers late-afternoon slots…" | `clean` | **yes** | no |
| Others | none | `absent` | no | no |

`absent` and `blocked:` are deliberately distinct: *"the model never saw it"* and *"there was nothing to see"* must not look identical in an audit.

**Screened notes are dropped, never rewritten.** Sanitizing an attacker's text and then trusting the rewrite is a worse position than proceeding without the field.

**Two findings:**
- `gcloud model-armor templates list|create` returns `PERMISSION_DENIED` on a project where the REST API works fine — it targets a different host. Templates are created via REST in `deploy.sh`.
- **The layer was inert when first wired.** `worker.py` never put `notes` into the agent state, so every note reported `absent` and nothing was ever screened. Caught only by running it end to end against the seeded payload; no unit test would have noticed, because each half was individually correct.

**Memory Bank** remains unbuilt — it is contingent on the Fleet track decision (GAP-012), and the Agent Engine spike already proved the surface exists.

### GAP-010 — No demo assets
**Category:** judging · **Status:** **PARTIALLY RESOLVED 2026-08-16** · **Spec:** [F-13](CLAUDE.md#f-13)

**Done:** synthetic demo tenant (`scripts/seed_demo_tenant.py`, seeded and used for every end-to-end run since F-6); architecture diagram ([docs/architecture.svg](docs/architecture.svg)) showing Gemini, backend, database and frontend as the submission requires; a 4-minute shot list with exact commands ([docs/demo-script.md](docs/demo-script.md)); and paste-ready Devpost text including the prior-work disclosure ([docs/devpost-submission.md](docs/devpost-submission.md)).

**Still open — and only the operator can do it:** record the video, and take the Cloud Run / Cloud SQL / Scheduler console screenshots that the rules require to be visible on camera.

**Two things the script gets right that are easy to get wrong on the day:**
- `SEGMENT_MAX_CLIENTS` must be raised to 25 before recording. At 4 the publisher takes the four *most lapsed* clients, and neither the opted-out nor the cooldown client is among them — **the refusals would not appear on camera at all**, which would cut the strongest 60 seconds.
- The dashboard is IAM-only, so a browser cannot satisfy both the Bearer and Basic challenges. Use `gcloud run services proxy`.

### GAP-012 — Track choice and prize category undecided
**Category:** submission · **Status:** **PARTIALLY RESOLVED 2026-08-16** · **Owner:** human, not a code model

1. **Track — evidence is in.** F-1 step 5 succeeded: Agent Engine deployed on the first real attempt, serves the production agent shape, and exposes Memory Bank (`add_session_to_memory`) plus managed sessions. The pre-registered decision rule was *Agent Engine works → Fleet*, so the recommendation is **The Fortified Enterprise Fleet** — thinner field, and three named components (Agent Runtime, Memory Bank, Agent Identity) are in hand rather than aspirational. Awaiting the operator's confirmation.
2. **Startup Excellence** ($20k) still open, and still only the operator can settle it: it requires an incorporated org and a corporate email address, and the Devpost registration uses a personal Gmail. The gcloud account is a RelayOps-branded Gmail, which is **not** a corporate address for this purpose.

**Impact:** filing in the wrong category forfeits the most winnable prize.

---

## Low

### GAP-013 — Cost ceiling is per-run, not per-month
**Category:** ops · **Status:** **PARTIALLY RESOLVED 2026-08-16**

`SEGMENT_MAX_CLIENTS` / `OUTREACH_MAX_DRAFTS` cap one run. A misconfigured Scheduler firing hourly across tenants would still bill hourly. `DRY_RUN` defaults true, which limits the blast radius but is not a budget.

**Done — the alerting half.** A monthly budget scoped to `relayops-fleet` (50 CAD, calendar month) now emails billing admins at 50%, 90% and 100% of actual spend, plus at a **forecasted** 75% so a runaway loop is caught on the way up rather than after it lands.

An existing `relayops` budget on the same billing account was scoped to a different project entirely and did **not** cover `relayops-fleet`, so fleet spend had been unmonitored since the project was created.

**Still open — the ceiling half.** A budget alert notifies; it does not stop anything. Google Cloud has no native hard spend cap, so an actual ceiling means either a token counter the worker enforces itself before calling a model, or a billing-alert Pub/Sub hook that disables the Scheduler job. Neither is built, and the alert must not be mistaken for one.

---

## Explicitly out of scope (checked, not issues)

Investigated and deliberately excluded, so no future session re-opens them:

- **The website audit engine** (`relayops.audit`), PageSpeed, CrUX, headless crawling — mature and working in `relayops-prod`, and irrelevant to a Track-2 demo. Porting it is the most tempting scope creep available.
- **The Track-1 prospect pipeline** (Places fetch → enrich → pitch). Working today; it stays on the existing local stack. It also must not appear in a public demo video naming real businesses and their defects.
- **Slack surfaces** (`relayops-ai-rebooking-agent`). A second UI competing with the approval dashboard for demo minutes.
- **Actually sending SMS or email.** Not a gap — an architectural guarantee. There is no send path and there will not be one.
- **Multi-region / HA.** Judges ask for a working deployment, not an SLA.
- **Firestore.** Cloud SQL is already required, the schema is relational, and the Alembic migrations port directly. Adding Firestore for extra service coverage would be checkbox architecture.
