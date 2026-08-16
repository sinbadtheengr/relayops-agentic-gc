"""Outreach agent — ADK + Gemini, structured `OutreachDraftSet` output.

Adapts the campaign-template section supplied by
`callbacks.attach_template_section` into SMS + email copy for one client.
Output passes through `core.casl` guards before it is persisted, and lands
with `status='draft'`.

**This system never sends.** There is no send path in this repo. Approval
marks a draft; a human sends it out of band and clicks Mark sent, which
writes `contact_log` (starting the cooldown) BEFORE flipping the status — so
a failure can never produce a sent draft whose cooldown silently did not
start.

See CLAUDE.md F-7.
"""
from __future__ import annotations

# TODO(F-7): build the ADK LlmAgent (model: settings.gemini_outreach_model,
# output_schema: OutreachDraftSet, same callback set as segment.py).
