"""Segment agent — ADK + Gemini, structured `SegmentDecision` output.

Reached ONLY by clients who passed every gate in `core.gates`. Receives
finished features via `callbacks.attach_client_features` and is told
explicitly that those numbers are authoritative.

What the model decides: whether this client is worth contacting, which
priority tier, and which campaign offer fits.

What the model may NEVER decide: whether contacting them is permitted. That
is `core.gates`, it runs first, and its answer is final.

See CLAUDE.md F-7.
"""
from __future__ import annotations

# TODO(F-7): build the ADK LlmAgent.
#   - model:  settings.gemini_segment_model
#   - output_schema: SegmentDecision
#   - before_agent_callback: attach_client_features
#   - before_model_callback: sanitize_untrusted_fields  (F-9)
#   - after_model_callback:  log_agent_decision         (F-10)
