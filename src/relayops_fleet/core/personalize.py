"""Local re-join of the client's name. NO LLM CALLS, NO NETWORK.

The outreach agent never learns who it is writing to. It produces copy
carrying `{{first_name}}` exactly as the approved template does, and this
module substitutes the real name afterwards, in-process, on the way to the
database.

**Why the name is withheld rather than simply trusted to the model.** Gemini
>=3.5 is served only from Vertex's `global` endpoint, which routes to
whichever region has capacity, so a prompt may be processed outside Canada.
Lapse buckets, visit counts and spend are attributes; a name is an
identifier. Withholding it means the honest answer to a clinic owner asking
"where does my client list go?" is that the names never left. (GAP-014)

The other merge fields — clinic_name, booking_link, staff_name,
clinic_address, incentive — are deliberately NOT substituted here. Those
belong to the clinic and are filled in by whoever sends the message.

See CLAUDE.md F-7 and GAPS_AND_ISSUES.md GAP-014.
"""
from __future__ import annotations

import re

from ..schemas import OutreachDraftSet

# Tolerates {{first_name}}, {{ first_name }} and {{First_Name}}: the model
# copies the token through from the template, and a whitespace or case slip
# would otherwise ship a literal placeholder to a real client.
FIRST_NAME_TOKEN = re.compile(r"\{\{\s*first_name\s*\}\}", re.IGNORECASE)


def substitute_first_name(text: str, first_name: str) -> str:
    return FIRST_NAME_TOKEN.sub(first_name, text)


def apply_merge_fields(draft: OutreachDraftSet, *, first_name: str) -> OutreachDraftSet:
    """Fill in the one merge field this system owns, across every field.

    The subject line is included: "It's been a minute, {{first_name}}" is a
    real template subject, and an unsubstituted placeholder there is the most
    visible way to look automated to the person you are trying to win back.
    """
    return draft.model_copy(
        update={
            "sms": substitute_first_name(draft.sms, first_name),
            "email_subject": substitute_first_name(draft.email_subject, first_name),
            "email_body": substitute_first_name(draft.email_body, first_name),
        }
    )


def unsubstituted_tokens(draft: OutreachDraftSet) -> bool:
    """True if any first_name placeholder survived. Used to fail loudly."""
    return any(
        FIRST_NAME_TOKEN.search(value)
        for value in (draft.sms, draft.email_subject, draft.email_body)
    )
