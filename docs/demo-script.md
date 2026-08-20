# 4-minute demo video — shot list

**Submission requirement:** ~4 minutes covering problem, value, a working demo, and visible proof of Google Cloud deployment.

**The structure is deliberate: lead with the refusals.** Every other entry demos an agent doing things. This one demos an agent *declining* to act, and then shows the receipt. That is the whole differentiator and it belongs in the first 90 seconds, not the last 20.

---

## Before recording

```bash
# 1. Reset the demo tenant so counts are clean on camera.
python scripts/seed_demo_tenant.py --reset

# 2. Raise the cap so the gated clients are actually included.
#    At SEGMENT_MAX_CLIENTS=4 the publisher takes the four MOST lapsed, and
#    the opted-out and cooldown clients are not among them — the refusals
#    would not appear on camera at all.
gcloud run jobs update relayops-publisher --project relayops-fleet --region us-central1 \
  --update-env-vars SEGMENT_MAX_CLIENTS=25

# 3. Rebuild the clinic's campaign memory from the seeded prior wave.
#    Without this the memory verdict on camera reads "empty", which is
#    honest but shows nothing. Takes seconds.
python scripts/sync_campaign_memory.py

# 4. Get the dashboard password (do NOT show this on camera).
gcloud secrets versions access latest --secret relayops-dashboard-password --project relayops-fleet

# 5. Open the dashboard. Cloud Run is IAM-only, so use the proxy —
#    a browser cannot satisfy both the Bearer and Basic challenges.
gcloud run services proxy relayops-dashboard --region us-central1 --project relayops-fleet
```

Have open in tabs: Cloud Run console, Cloud SQL console, the dashboard via proxy, a terminal.

---

## 0:00–0:35 · The problem, in the operator's own words

> "Small clinics lose more revenue to clients who quietly stop coming back than to anything else. The list of those clients is already sitting in their booking software. What's missing is the nightly work: deciding who's worth contacting, what to say to each of them, and proving afterwards which visits the campaign actually caused."

> "I charge $50 per client who books and shows up, capped at $1,500. So this system's output isn't a summary — it's an invoice someone is going to argue with."

**On screen:** the clinic's client list in the dashboard, 11 lapsed clients.

## 0:35–1:05 · What it is

> "Every night, per clinic, one Pub/Sub message per lapsed client. Each one runs a small agent pipeline on Cloud Run: gates, then two Gemini agents, then compliance guards, then a human."

**On screen:** the architecture diagram ([docs/architecture.svg](architecture.svg)). Trace the path once with the cursor — Scheduler → job → Pub/Sub → worker → Cloud SQL → dashboard.

## 1:05–2:10 · The live run

```bash
gcloud run jobs execute relayops-publisher --project relayops-fleet --region us-central1 --wait
```

**On screen:** the job's log output — `published: 11`.

> "That's the fan-out. Now the interesting part."

Then run the decision query (keep this in shell history so it is one keystroke):

```bash
python - <<'EOF'
from sqlalchemy import text
from relayops_fleet.config import get_settings
from relayops_fleet.db import repo
e = repo.build_engine(get_settings().database_url)
with repo.unguarded(), e.connect() as c:
    cid = c.execute(text("SELECT id FROM clinics WHERE name='Glow Aesthetics (demo)'")).scalar()
    for r in c.execute(text(
        "SELECT agent_name, decided_by, coalesce(gate_reason,'-') gate, tokens "
        "FROM agent_decisions WHERE clinic_id=:c ORDER BY id"), {'c': cid}):
        print(f"{r.agent_name:9} {r.decided_by:5} {r.gate:10} {r.tokens:>6} tokens")
EOF
```

**This is the moment the video turns on.** Point at the two `rule` rows:

> "These two clients were never contacted. One replied STOP months ago; one was messaged three days ago and is inside the cooldown. Both decisions were made in Python, before any model ran — look at the token column. **Zero.** The model was never asked, because whether someone *may* be contacted is not a judgement call I'm willing to delegate."

## 2:10–2:40 · What the agents did produce

**On screen:** the dashboard drafts queue.

> "For the clients who did pass, two Gemini agents ran. The first decides whether they're worth contacting and which approved campaign template fits. The second writes the copy — and it adapts a template the clinic already signed off, it doesn't invent an offer."

Point at a VIP draft:

> "This one's a VIP, top 20% of spend at this clinic. Notice there's no discount anywhere in it — VIPs get priority booking instead. Discounting someone who already pays full price just teaches them to wait for the discount. The model is told that, and then a rule checks it anyway."

Click **Why this draft?**

> "Every draft links to the exact model call behind it: the model, the token count, the latency, and the facts it was shown. When a clinic owner asks why it said that to their client, the answer is a row, not a shrug."

## 2:40–2:55 · What it remembers *(Fleet track: Memory Bank)*

Still on the decision view, point at `memory_verdict: used:3`, then show the facts in the input panel.

> "This clinic ran a wave last month, and the outcomes are recorded. So before writing anything, the agent is told what actually converted here — Segment D copy by SMS to VIP clients: three contacted, two came back. That's computed from the outcome log, not remembered by the model."

> "Two things about that memory. It's aggregate — no client is named in it, ever. And it's scoped to this clinic: another clinic's run cannot retrieve it, because the store matches the scope exactly. Cross-tenant memory is a data leak wearing a feature's clothes."

> "And it can't change the offer. It's allowed to influence tone and channel. The approved template is still the only thing that can put an offer in a message."

## 2:55–3:20 · The human gate

Point at the approve button.

> "The button says *Approve — does not send*, and it means it. There is no send path in this codebase. Approving marks a draft; a person sends it and clicks *I sent this*, which starts the 14-day cooldown **before** the draft can look sent. Get that order wrong and someone gets messaged twice."

## 3:20–3:50 · Google Cloud proof *(required on camera)*

**Cloud Run console:** `relayops-worker`, `relayops-dashboard`, `relayops-publisher`.
**Cloud SQL console:** `relayops-fleet-db`.
**Cloud Scheduler:** `relayops-nightly`, ENABLED, `0 9 * * *`.
**Vertex AI → Agent Engine:** `relayops-fleet-memory` — the Memory Bank host.

> "Gemini 3.7 and 3.5 Flash on Vertex, two ADK agents, Agent Engine holding per-clinic memory, Cloud Run, Cloud SQL, Pub/Sub with dead-lettering, Cloud Scheduler. One script provisions all of it."

## 3:50–4:00 · Close

> "Most agent demos are impressive because of what the agent does. I think this one is credible because of what it's forbidden to do — decide who may be contacted, do arithmetic, send anything, or act unlogged. That's what makes it something I can point at a real clinic's client list on Monday."

---

## Do not show on camera

- The dashboard password, the DB password, or `.env`.
- Any real client or prospect data. **The demo tenant is entirely synthetic** — every name and number in `scripts/seed_demo_tenant.py` is generated.
- The Cloud SQL authorized-networks list (it carries a home IP). Remove it first:
  ```bash
  gcloud sql instances patch relayops-fleet-db --project relayops-fleet --clear-authorized-networks
  ```

## After recording

```bash
# Put the cap back so an unattended nightly run cannot spend unexpectedly.
gcloud run jobs update relayops-publisher --project relayops-fleet --region us-central1 \
  --update-env-vars SEGMENT_MAX_CLIENTS=4

# Or stop the schedule entirely until judging.
gcloud scheduler jobs pause relayops-nightly --project relayops-fleet --location us-central1
```
