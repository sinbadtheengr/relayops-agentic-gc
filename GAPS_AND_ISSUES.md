# GAPS_AND_ISSUES — RelayOps Revenue Recovery Fleet

**Verified against:** the working tree at scaffold time (2026-08-15), no commits yet.

> **How this file differs from the usual one.** The standard version of this
> document catalogs verified defects in existing code at a named commit. This
> repo has no code yet, so every entry below is a gap between *what exists*
> and *what a qualifying, deployable submission requires*. They are unbuilt
> work, not observed bugs, and each says so.
>
> **Once code lands, this file reverts to the normal contract**: no gap may be
> listed that was not verified against real code at a named commit, with
> `file:line` links.

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
**Category:** qualification · **Status:** **PARTIALLY RESOLVED 2026-08-16** · **Spec:** [F-1](CLAUDE.md#f-1), [F-2](CLAUDE.md#f-2)
**Evidence:** [docs/F1-qualification-evidence.md](docs/F1-qualification-evidence.md)

Done: project `relayops-fleet` created and billed; 10 APIs enabled; an ADK agent carrying the real F-7 shape (strict `output_schema` + `before_agent_callback`) **deployed to Cloud Run and verified end to end** — HTTP 200, exact schema match, injected facts cited, VIP not discounted. Service is `--no-allow-unauthenticated`.

Still open: Cloud SQL, Pub/Sub topics, Cloud Scheduler, and the real (non-spike) services. Those belong to F-2 and F-6.

**Remaining risk unchanged:** Cloud SQL connectivity and IAM are still where a solo timeline dies. Do them next, not last.

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
**Category:** implementation · **Status:** OPEN · **Spec:** [F-3](CLAUDE.md#f-3), [F-5](CLAUDE.md#f-5), [F-7](CLAUDE.md#f-7), [F-11](CLAUDE.md#f-11)

`importer.py`, `features.py`, `casl.py`, `attribution.py` are stubs. All four have tested equivalents in `relayops-prod`; this is porting, not design.

**Impact:** without it there is nothing for the agents to reason over and no invoice to defend.
**Fix:** M2, and port the tests with the code.

### GAP-005 — LangGraph → ADK port not done
**Category:** qualification · **Status:** OPEN · **Spec:** [F-7](CLAUDE.md#f-7)

`relayops-prod` orchestrates segmentation with LangGraph, which is **not** on the hackathon's accepted framework list. The agents here are stubs.

**Impact:** fails a hard requirement if left; also the substantive "new work" that justifies the newly-created-project rule.
**Fix:** rebuild segment + outreach as ADK `LlmAgent`s. Keep `compute_features` (`segment_agent.py:86`); discard `decide()` / `build_graph()` (`:111`, `:172`) — that is precisely what ADK replaces.

### GAP-006 — No async fabric
**Category:** implementation · **Status:** OPEN · **Spec:** [F-6](CLAUDE.md#f-6)

The hackathon theme is agents that *"run in the background… asynchronously"*. Today the design is a synchronous CLI inherited from `relayops-prod`.

**Impact:** the theme is the 40% criterion. A batch loop demoed as a "fleet" reads as one.
**Fix:** publisher job → Pub/Sub → push worker, with DLQ and idempotent writes.

### GAP-007 — No human approval surface
**Category:** implementation · **Status:** OPEN · **Spec:** [F-8](CLAUDE.md#f-8)

"Never sends" is only a real guarantee if the approval step is a working product surface.

**Impact:** the differentiating claim is unbacked; the demo has nothing to show at the end of the pipeline.
**Fix:** M5. Include the skipped-clients view — it is the fastest way to demonstrate rule-not-model gating.

### GAP-014 — The qualifying models force a global endpoint, against a Canadian data-residency posture
**Category:** compliance · **Status:** OPEN · **Discovered:** 2026-08-15 by F-1 · **Spec:** [F-1](CLAUDE.md#f-1)

Every Gemini ≥3.5 model is served only on the `global` endpoint (GAP-001, finding 2). Vertex's global endpoint routes requests to whichever region has capacity, so **client first names, lapse history and spend go wherever Google routes them** — not to a Canadian or even a US region by guarantee.

RelayOps sells to compliance-conscious GTA clinics, and the marketing site makes explicit PIPEDA/CASL claims. PIPEDA permits cross-border processing, but it requires the organization to remain accountable and to be transparent about it — and a clinic owner asking "where does my client list go?" deserves a true answer.

**Impact on the hackathon:** none. **Impact on the business:** the privacy policy and the FAQ's data-handling answer both become inaccurate the moment a real clinic's data flows through this.

**Fix (before the first real tenant, not before the demo):** either
(a) send only de-identified features to the model — the segment agent already receives computed numbers plus a first name, so dropping the name and rejoining locally is a small change and removes most of the exposure; or
(b) pin to a regional model (`gemini-2.5-pro` in `northamerica-northeast1`) for production tenants while the hackathon build stays on global; or
(c) keep global and update the privacy policy and clinic-facing FAQ to state cross-border processing plainly.

Option (a) is preferred: it is the only one that makes the question moot rather than disclosed.

### GAP-009 — No reproducible deployment path
**Category:** judging · **Status:** OPEN · **Spec:** [F-12](CLAUDE.md#f-12)

30% of the score is *Demo & Production Readiness*, explicitly including reproducibility. `deploy/deploy.sh` is a comment outline.

**Impact:** direct score loss on nearly a third of the rubric.
**Fix:** M7 — but write each step as it is first performed by hand, not from memory on day 15.

---

## Medium

### GAP-008 — No Model Armor, no per-role service accounts, no Memory Bank
**Category:** governance · **Status:** OPEN · **Spec:** [F-9](CLAUDE.md#f-9)

The Fortified Enterprise Fleet framing and the Best Architectural Design prize both rest on this layer. The injection surface is real: clinic exports carry free-text `notes` that reach a prompt.

**Impact:** loses the strongest differentiator; keeps a genuine injection path open.
**Fix:** M6. The dashboard service account must not hold `aiplatform.user` — it approves, it does not generate.

### GAP-010 — No demo assets
**Category:** judging · **Status:** OPEN · **Spec:** [F-13](CLAUDE.md#f-13)

No architecture diagram, no video, no Devpost text, no synthetic demo tenant.

**Impact:** an unshown system scores as an unbuilt one.
**Fix:** M7. Build the synthetic tenant early (M2) so the demo runs against it all the way through.

### GAP-012 — Track choice and prize category undecided
**Category:** submission · **Status:** **PARTIALLY RESOLVED 2026-08-16** · **Owner:** human, not a code model

1. **Track — evidence is in.** F-1 step 5 succeeded: Agent Engine deployed on the first real attempt, serves the production agent shape, and exposes Memory Bank (`add_session_to_memory`) plus managed sessions. The pre-registered decision rule was *Agent Engine works → Fleet*, so the recommendation is **The Fortified Enterprise Fleet** — thinner field, and three named components (Agent Runtime, Memory Bank, Agent Identity) are in hand rather than aspirational. Awaiting the operator's confirmation.
2. **Startup Excellence** ($20k) still open, and still only the operator can settle it: it requires an incorporated org and a corporate email address, and the Devpost registration uses a personal Gmail. The gcloud account is `relayops.ca@gmail.com` — a RelayOps-branded Gmail is **not** a corporate address for this purpose.

**Impact:** filing in the wrong category forfeits the most winnable prize.

---

## Low

### GAP-013 — Cost ceiling is per-run, not per-month
**Category:** ops · **Status:** OPEN

`SEGMENT_MAX_CLIENTS` / `OUTREACH_MAX_DRAFTS` cap one run. A misconfigured Scheduler firing hourly across tenants would still bill hourly. `DRY_RUN` defaults true, which limits the blast radius but is not a budget.

**Fix:** a GCP budget alert during M1; a monthly token ceiling later.

---

## Explicitly out of scope (checked, not issues)

Investigated and deliberately excluded, so no future session re-opens them:

- **The website audit engine** (`relayops.audit`), PageSpeed, CrUX, headless crawling — mature and working in `relayops-prod`, and irrelevant to a Track-2 demo. Porting it is the most tempting scope creep available.
- **The Track-1 prospect pipeline** (Places fetch → enrich → pitch). Working today; it stays on the existing local stack. It also must not appear in a public demo video naming real businesses and their defects.
- **Slack surfaces** (`relayops-ai-rebooking-agent`). A second UI competing with the approval dashboard for demo minutes.
- **Actually sending SMS or email.** Not a gap — an architectural guarantee. There is no send path and there will not be one.
- **Multi-region / HA.** Judges ask for a working deployment, not an SLA.
- **Firestore.** Cloud SQL is already required, the schema is relational, and the Alembic migrations port directly. Adding Firestore for extra service coverage would be checkbox architecture.
