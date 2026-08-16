# F-1 qualification evidence

**Run:** 2026-08-15 · **Project:** `relayops-prod` (525399925354) · **Account:** relayops.ca@gmail.com
**Reproduce:** `PYTHONPATH=src python scripts/spike_f1.py`

---

## Finding 1 — `gemini-3.5-pro` does not exist

The scaffold assumed a Pro-tier 3.5 model. It is not real. Full Gemini catalogue visible to this project:

```
gemini-2.5-flash / -flash-lite / -pro          (regional, callable)
gemini-3-flash-preview, gemini-3-pro-preview
gemini-3.1-flash-lite, gemini-3.1-pro-preview
gemini-3.5-flash, gemini-3.5-flash-lite
gemini-3.6-flash
gemini-3.7-flash
```

**No Pro-tier model at ≥3.5 exists on any accessible endpoint** (checked `global`, `us-central1`, `us-east5`, `northamerica-northeast1`).

Consequence: the planned Pro-for-segment / Flash-for-outreach split is not available. Both tiers are flash — newest for the judgement call, older and cheaper for high-volume templated copy.

## Finding 2 — ≥3.5 models are global-endpoint only

This is the trap, and it cost the first spike run:

| Endpoint | `models.list()` shows it | `generate_content` works |
|---|---|---|
| `us-central1` | ✅ yes | ❌ **404 NOT_FOUND** |
| `global` | ✅ yes | ✅ yes |

`gemini-2.5-flash` works fine regionally, which is why `relayops-prod` has never hit this.

**A model appearing in `models.list()` is not proof it is callable.** Any future model change must be verified with a real call, not a catalogue listing.

`GOOGLE_CLOUD_LOCATION=global` is now the default in `.env.example` and `config.py`.

## Finding 3 — structured output holds on all three candidates

One real call each, `response_schema=SegmentDecision` (`extra='forbid'`, so any drift raises):

| Model | Latency | Tokens | Strict parse | Verdict |
|---|---|---|---|---|
| `gemini-3.7-flash` | 4,487 ms | 912 | ✅ | **PASS** — chosen for segment |
| `gemini-3.6-flash` | 5,898 ms | 1,362 | ✅ | PASS |
| `gemini-3.5-flash` | 6,861 ms | 1,399 | ✅ | **PASS** — chosen for outreach |

The newest model was also the fastest and the cheapest in tokens.

All three respected the authoritative-facts instruction — each cited the supplied `lifetime_spend_cents` and `vip_threshold_cents` verbatim in `reasoning` rather than recomputing — and none offered a VIP a discount.

## Finding 4 — the VIP guard cannot substring-match "discount"

`gemini-3.6-flash` returned the offer `"Segment A VIP we-miss-you, non-discount perk"`. That is **correct, compliant copy**, and the spike's naive `"discount" in offer` check marked it a failure.

**Carried into F-5 as a requirement:** `core/casl.py::flag_vip_discount` must strip negated forms (`non-discount`, `no discount`, `without discount`, `no incentive`) before testing. A guard that flags compliant copy buries reviewers in false `[NEEDS REVIEW]` badges, and a reviewer who learns to ignore the badge is worse than no badge.

Found by a spike on day 1 rather than by a clinic on day 30.

---

## Settled configuration

```
GOOGLE_CLOUD_LOCATION=global
GEMINI_SEGMENT_MODEL=gemini-3.7-flash
GEMINI_OUTREACH_MODEL=gemini-3.5-flash
```

## Finding 5 — a fresh project 403s until the ADC quota project is moved

After creating `relayops-fleet` and enabling `aiplatform`, every model call returned:

```
403 PERMISSION_DENIED: Permission 'aiplatform.endpoints.predict' denied
```

The account holds `roles/owner` on the project, so this was not IAM. The cause was ADC: `application_default_credentials.json` still carried `quota_project_id: relayops-prod`. Fix:

```bash
gcloud auth application-default set-quota-project relayops-fleet
```

**This must be a numbered step in `deploy/deploy.sh`.** It is invisible from the console, presents as a permissions error, and would cost a judge reproducing the project a long detour.

## Finding 6 — ADK runs the real agent shape, not just hello-world

`scripts/spike_f1_adk.py` builds an `LlmAgent` with `output_schema=SegmentDecision`, `output_key`, and a `before_agent_callback` that injects Python-computed facts into session state — the exact F-7 production shape.

Result: **PASS**. Strict parse succeeded, and the model's `reasoning` cited the injected `lifetime_spend_cents` verbatim, confirming state injection reached the prompt (the assertion the spike actually checks — a passing parse alone would not have proved the callback fired).

ADK 2.7.0 / google-genai 2.18.1 / pydantic 2.13.4.

## Finding 7 — `adk deploy` overwrites the Vertex location with the Cloud Run region

The first deployed revision returned HTTP 500. Logs showed the GAP-001 404 again, from inside the container:

```
404 NOT_FOUND: Publisher model
  projects/relayops-fleet/locations/us-central1/.../gemini-3.7-flash
```

`adk deploy cloud_run` injects `GOOGLE_CLOUD_LOCATION=<deploy region>` into the container environment, so the agent's `os.environ.setdefault(..., "global")` was a no-op and it asked `us-central1` for a global-only model.

**Rule for F-7:** the Cloud Run region and the Vertex location are different things and must never share a variable. `deploy/spike_agent/agent.py` now hard-assigns from a separate `VERTEX_LOCATION`, and the service also carries `GOOGLE_CLOUD_LOCATION=global` explicitly.

This failure is invisible locally — it only appears once deployed.

## Finding 8 — the deployed agent passes end to end

`POST /run` against the Cloud Run service, authenticated with an identity token (the service is `--no-allow-unauthenticated`):

| Check | Result |
|---|---|
| HTTP status | 200 |
| Output keys exactly match `SegmentDecision` (no drift, no missing) | ✅ |
| `reasoning` cites the injected `412000` / `280000` facts | ✅ |
| VIP received a non-discount offer | ✅ |

Sample: `"VIP Concierge Priority Booking"` — *"...well above the 280000 cents VIP threshold... she qualifies as high-priority Tier A for a non-discount VIP win-back outreach."*

## Finding 9 — string-matching model output produces false failures, twice

Both of this spike's "failures" were bugs in the *checks*, not the model:

1. `"discount" in offer` flagged the compliant phrase **"non-discount perk"**.
2. `"412000" in reasoning` missed **"412,000 cents"** — the model added a thousands separator.

Two instances in one day, from a guard style the production `core/casl.py` is built on. **Carried into F-5:** normalize separators and strip negated forms before testing, and unit-test each guard against copy that *should not* trip it — not only copy that should.

## Finding 10 — Agent Engine deploys and serves; `Deploy failed` was a lie

`adk deploy agent_engine` printed:

```
Deployed to Agent Platform: projects/845922823378/locations/us-central1/reasoningEngines/1399240146775179264
...
Deploy failed: 'charmap' codec can't encode character '\U0001f389' in position 2
```

The deployment **succeeded**. The CLI then crashed printing a 🎉 emoji to a cp1252 Windows console, and still exited 0. Two traps in one line: a false failure message, and an exit code that agrees with neither.

**Set `PYTHONUTF8=1` for every ADK CLI call on Windows** — the same convention already used in `relayops-internal`. And never trust `adk deploy`'s exit code; verify the resource.

Verified by real query against the deployed instance:

| Check | Result |
|---|---|
| `create_session` + `stream_query` | ✅ |
| Output keys exactly match `SegmentDecision` | ✅ |
| `reasoning` cites injected facts | ✅ |
| VIP not discounted | ✅ |

The instance also exposes `add_session_to_memory` / `async_add_session_to_memory` — **Memory Bank is available**, which is the F-9 per-clinic campaign-memory surface and a named Fleet-track component.

Note this also confirms the Finding 7 fix works in *both* runtimes: the Agent Engine instance runs in `us-central1` while the agent's hard-assigned `VERTEX_LOCATION=global` still routes the model call correctly.

## Track decision (GAP-012)

Per F-1's acceptance criterion — *Agent Engine worked → file under Fortified Enterprise Fleet* — **the condition is met.** Agent Engine deployed on the first real attempt, serves the production agent shape, and provides Memory Bank plus managed sessions.

Recommendation: **The Fortified Enterprise Fleet**. It is the thinner field, and three of its named components (Agent Runtime, Memory Bank, Agent Identity) are now demonstrably in hand rather than aspirational.

## Environment as provisioned

| | |
|---|---|
| Project | `relayops-fleet` (845922823378), billing linked |
| APIs | aiplatform, run, sqladmin, pubsub, cloudscheduler, eventarc, cloudbuild, artifactregistry, logging, secretmanager |
| Cloud Run region | `us-central1` |
| Vertex location | `global` |

Cloud Run region and Vertex location are independent and differ on purpose.

## Deployed resources (live, costing money)

| Resource | Id |
|---|---|
| Cloud Run service | `relayops-fleet-spike` (us-central1, `--no-allow-unauthenticated`) |
| Agent Engine instance | `reasoningEngines/1399240146775179264` (us-central1) |

Cloud Run scales to zero. **The Agent Engine instance may bill for standing capacity** — delete it if the gap to the next work session is long:

```bash
python -c "import vertexai; from vertexai import agent_engines; vertexai.init(project='relayops-fleet', location='us-central1'); agent_engines.get('projects/845922823378/locations/us-central1/reasoningEngines/1399240146775179264').delete(force=True)"
```

## F-1 status: complete

All five steps done. Open items move to their own features:

- **GAP-014**: `global` endpoint vs Canadian data residency (business decision, not a build blocker).
- **GAP-012**: track decision — evidence now supports Fleet; the Startup Excellence / corporate-email question remains open and is the operator's.
- Cloud SQL and Pub/Sub provisioning move to **F-2** and **F-6**.
