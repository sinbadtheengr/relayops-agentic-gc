"""Campaign template loading. NO LLM CALLS IN THIS MODULE.

`templates/campaign-templates.md` is approved copy — the clinic's voice, signed
off before any campaign runs. The outreach agent adapts a section of it; it
never invents an offer, because an invented offer is one the clinic has not
agreed to honour and may not be able to.

Ported from relayops-prod `src/relayops/pipeline/outreach.py:78` — see
CLAUDE.md F-7.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

TEMPLATES_PATH = Path(__file__).resolve().parents[3] / "templates" / "campaign-templates.md"

# lapse bucket -> campaign-template section header
SEGMENT_SECTION = {
    "lapsed_90_180": "## Segment A",
    "lapsed_180_365": "## Segment B",
    "lapsed_365_plus": "## Segment C",
}
VIP_SECTION = "## Segment D"


class TemplateSectionMissing(LookupError):
    """The requested section is not in the approved template file."""


@lru_cache
def _template_text() -> str:
    return TEMPLATES_PATH.read_text(encoding="utf-8")


def load_template_section(*, bucket: str | None, is_vip: bool) -> str:
    """Return the approved section for this client's segment.

    VIP wins over the lapse bucket: Segment D is the only section with no
    incentive in it, and a VIP routed to their bucket's section would be shown
    copy that offers money off.

    Raises rather than falling back to the whole file. relayops-prod returned
    the entire document on a miss, which quietly hands the model every
    segment's copy — including discount offers — and invites it to pick.
    """
    header = VIP_SECTION if is_vip else SEGMENT_SECTION.get(bucket or "")
    if header is None:
        raise TemplateSectionMissing(
            f"no approved template section for bucket {bucket!r}; "
            "a client outside the lapse buckets should not have reached outreach"
        )
    for chunk in _template_text().split("\n---\n"):
        if header in chunk:
            return chunk.strip()
    raise TemplateSectionMissing(f"section {header!r} not found in {TEMPLATES_PATH.name}")
