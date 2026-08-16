"""Running one ADK agent and capturing what the decision log needs.

The worker calls these. They return the parsed, schema-valid output plus the
model, token count and latency — everything `obs.decisions.log_agent_decision`
requires, so a caller cannot log a decision while omitting what it cost.

See CLAUDE.md F-7.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, TypeVar

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from google.genai import types
from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)

APP_NAME = "relayops-fleet"


class AgentProducedNothing(RuntimeError):
    """The agent finished without a final response.

    Treated as a hard failure rather than an empty draft: a silent blank would
    reach the approval queue looking like the model had nothing to say.
    """


@dataclass(frozen=True)
class AgentRun:
    """One agent invocation and its cost, ready for the decision log."""

    output: BaseModel
    model: str
    tokens: int
    latency_ms: int
    raw_text: str


async def run_agent(
    agent: LlmAgent,
    *,
    state: dict[str, Any],
    message: str,
    schema: type[T],
    user_id: str = "worker",
) -> AgentRun:
    """Run `agent` with `state` installed, and strict-parse its output.

    The session is created WITH the state already populated, so the
    before_agent_callback sees a complete picture on its first and only run.
    """
    runner = InMemoryRunner(agent=agent, app_name=APP_NAME)
    session = await runner.session_service.create_session(
        app_name=APP_NAME, user_id=user_id, state=state
    )

    started = time.monotonic()
    final_text: str | None = None
    tokens = 0

    async for event in runner.run_async(
        user_id=user_id,
        session_id=session.id,
        new_message=types.Content(role="user", parts=[types.Part(text=message)]),
    ):
        usage = getattr(event, "usage_metadata", None)
        if usage is not None:
            # Accumulate: a retried or multi-step turn bills for every call,
            # and the decision row should show the true cost.
            tokens += getattr(usage, "total_token_count", 0) or 0
        if event.is_final_response() and event.content and event.content.parts:
            text = event.content.parts[0].text
            if text:
                final_text = text

    latency_ms = int((time.monotonic() - started) * 1000)
    if final_text is None:
        raise AgentProducedNothing(f"{agent.name} returned no final response")

    return AgentRun(
        output=schema.model_validate_json(final_text),
        model=str(agent.model),
        tokens=tokens,
        latency_ms=latency_ms,
        raw_text=final_text,
    )
