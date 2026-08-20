"""Agent Engine Memory Bank — per-clinic campaign memory.

The Fleet track's memory component (GAP-012). Writes what converted at one
clinic and reads it back for that clinic's next run, and for no other's.

**Scoping is the feature.** Every call carries
`{'app_name': 'relayops-fleet', 'user_id': 'clinic-<id>'}` and Memory Bank
matches that scope exactly — verified against the live service, including the
negative control that an unknown scope retrieves nothing. Cross-tenant memory
is a data leak wearing a feature's clothes, so the isolation is the service's
own primitive rather than a WHERE clause somebody has to remember.

**This degrades to absent, unlike Model Armor.** `armor.py` fails closed
because it stands between attacker-controlled text and a prompt. Memory
carries no attacker-controlled text — `core.campaign_memory` composes every
fact from enumerated values — so an unreachable Memory Bank costs tone
guidance and nothing else. Failing the run there would trade a real outage for
an imaginary risk. The verdict is recorded either way.

**Location is not `GOOGLE_CLOUD_LOCATION`.** Agent Engine is regional
(`us-central1`); Gemini >=3.5 is served from `global`. F-1 rule 2 already cost
a day to exactly this confusion, so they are separate settings.

See CLAUDE.md F-9.3.
"""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import dataclass

from ..config import get_settings
from ..core.campaign_memory import assert_deidentified

log = logging.getLogger(__name__)

APP_NAME = "relayops-fleet"

# What the agent is asked to recall. Retrieval is a similarity search inside
# the clinic's scope, so this is a topic, not a filter — the scope has already
# decided whose memories are visible.
RECALL_QUERY = "which approved template section and channel converted"


def scope_for(clinic_id: int) -> dict[str, str]:
    """The Memory Bank scope for one tenant. The only way memories are keyed."""
    # `isinstance(True, int)` is True, so bools are excluded explicitly — a
    # scope reading `clinic-True` would be a tenant nobody owns.
    if isinstance(clinic_id, bool) or not isinstance(clinic_id, int) or clinic_id <= 0:
        raise ValueError(f"clinic_id must be a positive int, got {clinic_id!r}")
    return {"app_name": APP_NAME, "user_id": f"clinic-{clinic_id}"}


@dataclass(frozen=True)
class Recall:
    """Retrieved facts plus the verdict that goes on the decision row.

    `absent` (not configured) and `unavailable` (configured, call failed) are
    deliberately distinct, for the same reason the staff-note verdicts are:
    "there was nothing to recall" and "we could not reach the store" must not
    look identical to someone auditing why a draft reads the way it does.
    """

    facts: tuple[str, ...]
    verdict: str


def is_configured() -> bool:
    return bool(get_settings().agent_engine_id)


def _require_project() -> str:
    """The project the Memory Bank lives in, or a message that says so.

    Without this the SDK falls back to the ADC default project and the failure
    surfaces as `404 The ReasoningEngine does not exist` — which sends you to
    check whether the instance was deleted, when the id was right all along and
    only the project was wrong. Same shape as F-1's false "Deploy failed": the
    error names the wrong thing, and an hour goes on the wrong hypothesis.
    """
    project = get_settings().google_cloud_project
    if not project:
        raise RuntimeError(
            "GOOGLE_CLOUD_PROJECT is not set; Memory Bank would be looked up in "
            "the ADC default project and report the engine as missing"
        )
    return project


def _service():
    from google.adk.memory import VertexAiMemoryBankService

    _require_project()
    settings = get_settings()
    return VertexAiMemoryBankService(
        project=settings.google_cloud_project,
        location=settings.agent_engine_location,
        agent_engine_id=settings.agent_engine_id,
    )


def _client():
    """The raw Vertex client, for the list/delete the ADK service does not wrap."""
    import vertexai

    return vertexai.Client(
        project=_require_project(), location=get_settings().agent_engine_location
    )


def _engine_name() -> str:
    return "reasoningEngines/" + get_settings().agent_engine_id


async def retrieve_clinic_memories(clinic_id: int) -> Recall:
    """Recall what converted at this clinic. Never raises."""
    if not is_configured():
        return Recall(facts=(), verdict="absent")

    scope = scope_for(clinic_id)
    try:
        response = await _service().search_memory(
            app_name=scope["app_name"], user_id=scope["user_id"], query=RECALL_QUERY
        )
    except Exception as exc:  # noqa: BLE001 - a memory outage must not stop a campaign
        log.warning("Memory Bank unreachable for clinic %s (%s); drafting without it", clinic_id, exc)
        return Recall(facts=(), verdict="unavailable")

    facts: list[str] = []
    for memory in response.memories:
        parts = getattr(getattr(memory, "content", None), "parts", None) or []
        text = (getattr(parts[0], "text", "") if parts else "") or ""
        if text.strip():
            facts.append(text.strip())
    if not facts:
        return Recall(facts=(), verdict="empty")
    return Recall(facts=tuple(facts), verdict=f"used:{len(facts)}")


async def replace_clinic_memories(clinic_id: int, facts: Sequence[str]) -> int:
    """Delete this clinic's memories and write `facts` in their place.

    Replace rather than append: facts are recomputed aggregates over the whole
    outcome log, so appending would leave last month's "3 of 4 converted"
    sitting beside this month's "5 of 9" as though both were currently true.

    Returns how many facts were written. Raises — a sync is an operator action
    that should fail loudly, unlike a retrieval during a campaign run.
    """
    if not is_configured():
        raise RuntimeError("AGENT_ENGINE_ID is not set; there is no Memory Bank to write to")

    for fact in facts:
        assert_deidentified(fact)

    scope = scope_for(clinic_id)
    delete_clinic_memories(clinic_id)

    if not facts:
        return 0

    from google.adk.memory.memory_entry import MemoryEntry
    from google.genai import types

    await _service().add_memory(
        app_name=scope["app_name"],
        user_id=scope["user_id"],
        memories=[
            MemoryEntry(
                author="user",
                content=types.Content(role="user", parts=[types.Part(text=fact)]),
            )
            for fact in facts
        ],
    )
    return len(facts)


def delete_clinic_memories(clinic_id: int) -> int:
    """Remove every memory in one clinic's scope. Returns how many went.

    Scope is matched here in Python because the list API does not filter by
    it. The comparison is exact and on the whole scope dict, so a clinic whose
    id is a prefix of another's cannot be caught by it.
    """
    scope = scope_for(clinic_id)
    client = _client()
    removed = 0
    for memory in client.agent_engines.memories.list(name=_engine_name()):
        existing = getattr(memory, "scope", None)
        if isinstance(existing, dict) and existing == scope:
            client.agent_engines.memories.delete(name=memory.name)
            removed += 1
    return removed


def recall_sync(clinic_id: int) -> Recall:
    """Blocking wrapper, for callers that are not already async."""
    return asyncio.run(retrieve_clinic_memories(clinic_id))
