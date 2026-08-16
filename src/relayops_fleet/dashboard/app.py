"""Operator approval surface — FastAPI on Cloud Run. The human gate.

Routes (F-8):

    GET  /                      tenant picker (operator sees all clinics)
    GET  /clinics/{id}/drafts   queue, tabbed by status with counts
    POST /drafts/{id}/approve   mark approved — DOES NOT SEND
    POST /drafts/{id}/reject
    POST /drafts/{id}/sent      writes contact_log, then flips status
    GET  /drafts/{id}/decision  the agent_decisions row behind this draft
    GET  /clinics/{id}/skipped  clients the gates excluded, with reasons
    GET  /clinics/{id}/invoice  computed attribution, with exclusions shown

`/clinics/{id}/skipped` exists because the drafts queue only shows who WAS
contacted. The compliance question is who was not, and why — and that view is
also the fastest demo of the point that rules, not the model, decide contact
eligibility.

Every route except the login requires DASHBOARD_PASSWORD (or IAP). The
approval surface exposes client PII; it never runs open.
"""
from __future__ import annotations

# TODO(F-8): implement.
