"""CASL enforcement and copy guards. NO LLM CALLS IN THIS MODULE.

Canada's Anti-Spam Legislation is the reason this product is architected the
way it is. Every commercial electronic message must identify the sender and
carry a working unsubscribe mechanism. That is not a prompt instruction — a
model that forgets it once creates real liability — so it is enforced in code
after generation, on every draft, unconditionally.

The guards are deliberately split in two kinds:

  * `enforce_casl` **repairs**. A missing STOP line is appended, because the
    draft is otherwise fine and a human is going to read it anyway.
  * `flag_*` **escalate**. Discount language in a VIP draft is a judgement
    call about the clinic's pricing, not something code should silently
    rewrite. It gets a badge and a human decides.

Ported from relayops-prod `src/relayops/pipeline/outreach.py:88-119`, with the
false-positive calibration described on `DISCOUNT_RE` — see CLAUDE.md F-5.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from ..schemas import OutreachDraftSet

STOP_LINE = "Reply STOP to opt out"
EMAIL_FOOTER = "{{clinic_name}} · {{clinic_address}} · Unsubscribe"
NEEDS_REVIEW = "[NEEDS REVIEW"

# Negated forms are stripped before any guard runs.
#
# "no incentive" and "non-discount perk" are COMPLIANT copy — they are the
# model stating it is not offering money off — and both were produced by
# gemini-3.7/3.6-flash during the F-1 spike. The inherited relayops-prod regex
# matches `\bdiscount\b` inside "non-discount" and `\bincentive\b` inside "no
# incentive", so porting it unchanged would have badged the exact phrasing the
# current models favour.
#
# A guard that flags correct copy is worse than no guard: reviewers learn to
# click past the badge, and then miss the real one.
_NEGATED = re.compile(
    r"\b(?:no|non|not|without|zero)[\s-]+(?:a\s+)?"
    r"(discounts?|incentives?|credits?|offers?|promotions?)\b",
    re.IGNORECASE,
)

# Hedges are the same problem for the overclaim guard: "we cannot guarantee
# availability, so book early" is careful, honest copy and the opposite of an
# overclaim, but it contains the bare word.
_HEDGED = re.compile(
    r"\b(?:cannot|can\s?not|can't|won't|will\s+not|no|without)\s+(guarantees?d?|cures?)\b",
    re.IGNORECASE,
)

# Money-off language a VIP draft must never contain.
#
# Two terms need trailing context, from an earlier guard audit in
# relayops-prod: bare `\bfree\b` matched "feel free to reply" — the commonest
# polite phrase in outreach — and bare `\bcredit\b` matched "credit card".
# Genuine offers ("a free treatment", "a welcome-back credit") still match.
DISCOUNT_RE = re.compile(
    r"(\$\s?\d"
    r"|\bdiscounts?\b"
    r"|\bcredits?\b(?!\s+card\b)"
    r"|\b\d{1,2}\s?%\s?off\b"
    r"|\bfree\b(?!\s+to\b)"
    r"|\bincentives?\b"
    r"|\{\{incentive\}\})",
    re.IGNORECASE,
)

# Copy that promises an outcome the clinic cannot guarantee. On a medical
# service this is a regulatory problem, not just marketing puffery.
OVERCLAIM_RE = re.compile(
    r"(\bguarantees?d?\b"
    r"|\brisk[\s-]?free\b"
    r"|\bcures?\b"
    r"|\bpermanent results?\b"
    r"|\bbest in\b"
    # No leading \b: '#' is a non-word character, so \b never matches before
    # it and the alternative silently never fired.
    r"|#\s?1\b"
    r"|\bno\.\s?1\b)",
    re.IGNORECASE,
)


def _searchable(text: str) -> str:
    """Text with negated offer phrases removed, for guard matching only."""
    return _NEGATED.sub(" ", text)


def contains_discount_language(text: str) -> bool:
    return bool(DISCOUNT_RE.search(_searchable(text)))


def contains_overclaim(text: str) -> bool:
    return bool(OVERCLAIM_RE.search(_HEDGED.sub(" ", _searchable(text))))


@dataclass(frozen=True)
class GuardedDraft:
    """A draft plus why a human needs to look at it.

    `needs_review` is returned as a value rather than left for the caller to
    infer from the text prefix: `outreach_drafts.needs_review` is a real
    column and the dashboard renders a badge from it (F-8). Sniffing a string
    prefix to rebuild a boolean we already knew is how the two drift apart.
    """

    draft: OutreachDraftSet
    needs_review: bool
    reasons: tuple[str, ...]


def enforce_casl(draft: OutreachDraftSet) -> OutreachDraftSet:
    """Guarantee the STOP line and the unsubscribe footer.

    Appends rather than rejects: a draft missing its footer is still useful
    copy, and a human reviews it either way. What must never happen is a
    compliant-looking draft reaching the approval queue without one.

    Idempotent — a redelivered Pub/Sub message must not stack two STOP lines.
    """
    sms = draft.sms.rstrip()
    if STOP_LINE.lower() not in sms.lower():
        if sms and not sms.endswith((".", "!", "?")):
            sms = f"{sms}."
        sms = f"{sms} {STOP_LINE}.".strip()

    body = draft.email_body.rstrip()
    if "unsubscribe" not in body.lower():
        body = f"{body}\n\n{EMAIL_FOOTER}"

    return draft.model_copy(update={"sms": sms, "email_body": body})


def flag_vip_discount(draft: OutreachDraftSet, *, is_vip: bool) -> tuple[OutreachDraftSet, bool]:
    """Badge a VIP draft that offers money off. Returns (draft, flagged).

    A VIP is an 80th-percentile spender. Discounting someone who already pays
    full price trains them to wait for the discount — it costs the clinic
    money on the client they can least afford to reprice. The model is told
    this; this function is what happens when it does it anyway.
    """
    if not is_vip:
        return draft, False

    updates: dict[str, str] = {}
    if contains_discount_language(draft.sms):
        updates["sms"] = f"{NEEDS_REVIEW}: discount language in VIP draft] {draft.sms}"
    if contains_discount_language(draft.email_body):
        updates["email_body"] = (
            f"{NEEDS_REVIEW}: discount language in VIP draft]\n{draft.email_body}"
        )
    if not updates:
        return draft, False
    return draft.model_copy(update=updates), True


def flag_overclaims(draft: OutreachDraftSet) -> tuple[OutreachDraftSet, bool]:
    """Badge copy that promises a medical or financial outcome."""
    updates: dict[str, str] = {}
    if contains_overclaim(draft.sms):
        updates["sms"] = f"{NEEDS_REVIEW}: overclaim] {draft.sms}"
    for field in ("email_subject", "email_body"):
        value = getattr(draft, field)
        if contains_overclaim(value):
            updates[field] = f"{NEEDS_REVIEW}: overclaim] {value}"
    if not updates:
        return draft, False
    return draft.model_copy(update=updates), True


def apply_copy_guards(draft: OutreachDraftSet, *, is_vip: bool) -> GuardedDraft:
    """Run every guard in order. This is what the outreach agent calls.

    CASL repair runs FIRST: the appended footer is trusted text that must not
    itself be scanned for offer language, and repairing after flagging would
    place the STOP line after the review prefix.
    """
    repaired = enforce_casl(draft)
    reasons: list[str] = []

    repaired, vip_flagged = flag_vip_discount(repaired, is_vip=is_vip)
    if vip_flagged:
        reasons.append("discount language in VIP draft")

    repaired, overclaim_flagged = flag_overclaims(repaired)
    if overclaim_flagged:
        reasons.append("overclaim")

    return GuardedDraft(draft=repaired, needs_review=bool(reasons), reasons=tuple(reasons))
