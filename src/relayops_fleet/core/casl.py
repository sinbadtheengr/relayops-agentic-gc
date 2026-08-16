"""CASL enforcement and copy guards. NO LLM CALLS IN THIS MODULE.

Canada's Anti-Spam Legislation is the reason this product is architected the
way it is. Every commercial electronic message must identify the sender and
carry a working unsubscribe mechanism. That is not a prompt instruction — a
model that forgets it once creates real liability, so it is enforced in code
after generation.

Port target: relayops-prod `src/relayops/pipeline/outreach.py:88-119`
(`enforce_casl`, `validate_vip_no_discount`) — see CLAUDE.md F-5.
"""
from __future__ import annotations

from ..schemas import OutreachDraftSet

STOP_LINE = "Reply STOP to opt out."
NEEDS_REVIEW = "[NEEDS REVIEW]"

# Words that turn a win-back note into a promise the clinic cannot keep.
OVERCLAIM_TERMS = (
    "guarantee",
    "guaranteed",
    "risk-free",
    "cure",
    "permanent results",
    "best in",
    "#1",
)


def enforce_casl(draft: OutreachDraftSet) -> OutreachDraftSet:
    """Append the STOP line and the unsubscribe footer if the model omitted them.

    Appends rather than rejects: a draft missing its footer is still useful
    copy, and a human reviews it either way. What must never happen is a
    compliant-looking draft reaching the approval queue without the footer.

    TODO(F-5): implement.
    """
    raise NotImplementedError("F-5")


def flag_vip_discount(draft: OutreachDraftSet, *, is_vip: bool) -> OutreachDraftSet:
    """Prefix NEEDS REVIEW when a VIP draft contains discount language.

    A VIP is an 80th-percentile spender. Discounting to someone who already
    pays full price trains them to wait for the discount — it costs the clinic
    money on the client they can least afford to reprice. The model is told
    this; this function is what happens when it does it anyway.

    TODO(F-5): implement.
    """
    raise NotImplementedError("F-5")


def flag_overclaims(draft: OutreachDraftSet) -> OutreachDraftSet:
    """Prefix NEEDS REVIEW when copy over-promises a medical or financial result.

    TODO(F-5): implement.
    """
    raise NotImplementedError("F-5")
