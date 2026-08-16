"""Operator approval surface — FastAPI on Cloud Run. The human gate.

This is where "the system never sends" stops being a claim and becomes a
mechanism: agents produce drafts, a person reads them, and a person sends
them out of band. Nothing in this application has a send path.

Two behaviours here are load-bearing rather than cosmetic:

* **Approve does not send.** It sets `status='approved'`. The button says so,
  because an operator who believes clicking Approve dispatched a message will
  eventually be very surprised.
* **Mark sent writes `contact_log` BEFORE flipping the status.** A failure
  between the two must never produce a sent draft whose cooldown silently did
  not start — that is how someone gets messaged twice.

Auth: HTTP Basic against `DASHBOARD_PASSWORD`, or Cloud Run IAP in front. With
neither, every route returns 503. This surface exposes client PII and does not
run open.

See CLAUDE.md F-8.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..config import get_settings
from ..core.attribution import money
from ..db import billing_repo, consent_repo, dashboard_repo
from ..db.campaign_repo import active_clinics
from ..db.models import Clinic
from ..db.repo import build_engine, build_sessionmaker, unguarded

TEMPLATES = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))

# No /docs, /redoc or /openapi.json: Cloud Run serves them before the Basic
# auth dependency runs, so on a surface that lists client PII they would
# publish the full route map to anyone who can reach the service.
app = FastAPI(
    title="RelayOps Fleet — approvals",
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
security = HTTPBasic(auto_error=False)

_Session = None


def _sessions():
    global _Session
    if _Session is None:
        _Session = build_sessionmaker(build_engine())
    return _Session


def require_operator(
    credentials: Annotated[HTTPBasicCredentials | None, Depends(security)],
) -> str:
    """Refuse to serve at all when no password is configured.

    Failing closed is the only safe default for a page that lists client
    names, phone numbers and message copy.
    """
    password = get_settings().dashboard_password
    if not password:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="DASHBOARD_PASSWORD is not set; this surface exposes client PII "
            "and will not run open.",
        )
    if credentials is None or not secrets.compare_digest(credentials.password, password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
            headers={"WWW-Authenticate": "Basic"},
        )
    return credentials.username or "operator"


def get_session() -> Session:
    with _sessions()() as session:
        yield session


def _clinic_or_404(session: Session, clinic_id: int) -> Clinic:
    with unguarded():
        clinic = session.get(Clinic, clinic_id)
    if clinic is None:
        raise HTTPException(status_code=404, detail="no such clinic")
    return clinic


# NOT /healthz: Cloud Run's frontend intercepts that path (verified
# 2026-08-16) and the route never reaches the app.
@app.get("/health")
def healthz() -> dict[str, str]:
    """Unauthenticated on purpose: Cloud Run needs it and it reveals nothing."""
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def tenant_picker(
    request: Request,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
):
    clinics = active_clinics(session)
    rows = [
        {
            "clinic": clinic,
            "counts": dashboard_repo.draft_counts(session, clinic_id=clinic.id),
        }
        for clinic in clinics
    ]
    return TEMPLATES.TemplateResponse(request, "index.html", {"rows": rows})


@app.get("/clinics/{clinic_id}/drafts", response_class=HTMLResponse)
def drafts(
    request: Request,
    clinic_id: int,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
    status_filter: str = "draft",
):
    clinic = _clinic_or_404(session, clinic_id)
    return TEMPLATES.TemplateResponse(
        request,
        "drafts.html",
        {
            "clinic": clinic,
            "counts": dashboard_repo.draft_counts(session, clinic_id=clinic_id),
            "status_filter": status_filter,
            "drafts": dashboard_repo.drafts_for_clinic(
                session, clinic_id=clinic_id, status=status_filter
            ),
            "statuses": dashboard_repo.DRAFT_STATUSES,
        },
    )


@app.get("/clinics/{clinic_id}/drafts/{draft_id}/decision", response_class=HTMLResponse)
def draft_decision(
    request: Request,
    clinic_id: int,
    draft_id: int,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
):
    clinic = _clinic_or_404(session, clinic_id)
    draft = dashboard_repo.get_draft(session, clinic_id=clinic_id, draft_id=draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="no such draft")
    return TEMPLATES.TemplateResponse(
        request,
        "decision.html",
        {
            "clinic": clinic,
            "draft": draft,
            "decision": dashboard_repo.decision_for_draft(
                session, clinic_id=clinic_id, draft=draft
            ),
        },
    )


@app.get("/clinics/{clinic_id}/skipped", response_class=HTMLResponse)
def skipped(
    request: Request,
    clinic_id: int,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
):
    """Who was NOT contacted, and why. The compliance view."""
    clinic = _clinic_or_404(session, clinic_id)
    return TEMPLATES.TemplateResponse(
        request,
        "skipped.html",
        {
            "clinic": clinic,
            "rows": dashboard_repo.skipped_clients(session, clinic_id=clinic_id),
            "reason_counts": dashboard_repo.gate_reason_counts(session, clinic_id=clinic_id),
        },
    )


@app.get("/clinics/{clinic_id}/invoice", response_class=HTMLResponse)
def invoice(
    request: Request,
    clinic_id: int,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
):
    """What this clinic owes, recomputed from the outcome log on every load.

    Nothing on this page is stored. A stored total would drift the moment a
    contact or an outcome was corrected, and the clinic would be arguing with
    a number nobody could reproduce.
    """
    clinic = _clinic_or_404(session, clinic_id)
    return TEMPLATES.TemplateResponse(
        request,
        "invoice.html",
        {
            "clinic": clinic,
            "summary": billing_repo.billing_summary(session, clinic_id=clinic_id),
            "money": money,
        },
    )


def _set_status(session: Session, *, clinic_id: int, draft_id: int, new_status: str) -> None:
    changed = dashboard_repo.set_draft_status(
        session, clinic_id=clinic_id, draft_id=draft_id, new_status=new_status
    )
    if changed == 0:
        # Either no such draft, or it belongs to another clinic. Both are 404
        # from here: telling an operator that a draft exists but is not theirs
        # leaks that another tenant has that client.
        raise HTTPException(status_code=404, detail="no such draft")


@app.post("/clinics/{clinic_id}/drafts/{draft_id}/approve")
def approve(
    clinic_id: int,
    draft_id: int,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
):
    """Mark approved. **This does not send anything.**"""
    _set_status(session, clinic_id=clinic_id, draft_id=draft_id, new_status="approved")
    session.commit()
    return RedirectResponse(f"/clinics/{clinic_id}/drafts", status_code=303)


@app.post("/clinics/{clinic_id}/drafts/{draft_id}/reject")
def reject(
    clinic_id: int,
    draft_id: int,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
):
    _set_status(session, clinic_id=clinic_id, draft_id=draft_id, new_status="rejected")
    session.commit()
    return RedirectResponse(f"/clinics/{clinic_id}/drafts", status_code=303)


@app.post("/clinics/{clinic_id}/drafts/{draft_id}/sent")
def mark_sent(
    clinic_id: int,
    draft_id: int,
    _operator: Annotated[str, Depends(require_operator)],
    session: Annotated[Session, Depends(get_session)],
    note: Annotated[str | None, Form()] = None,
):
    """Record that a human sent this out of band.

    Order is the whole point: `contact_log` is written FIRST, so the cooldown
    has started before the draft can look sent. Both live in one transaction,
    so a failure leaves neither.
    """
    draft = dashboard_repo.get_draft(session, clinic_id=clinic_id, draft_id=draft_id)
    if draft is None:
        raise HTTPException(status_code=404, detail="no such draft")

    consent_repo.log_contact(
        session,
        clinic_id=clinic_id,
        client_key=draft.client_key,
        channel=draft.channel,
        note=note or "sent from approval dashboard",
    )
    session.flush()
    _set_status(session, clinic_id=clinic_id, draft_id=draft_id, new_status="sent")
    session.commit()
    return RedirectResponse(f"/clinics/{clinic_id}/drafts", status_code=303)
