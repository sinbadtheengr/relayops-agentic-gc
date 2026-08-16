"""Model Armor screening — the second layer over clinic-supplied free text.

Sits behind `core.untrusted.screen_note`, which is deterministic and always
runs. This one is a network call to a managed Google service that catches the
phrasings a regex never will.

**It fails closed.** If Model Armor is configured but unreachable, the note is
dropped rather than passed through unscreened. A compliance boundary that
degrades to "allow" when a dependency is down is not a boundary — and the
thing being protected here is a clinic's name on an unauthorised offer.

When `MODEL_ARMOR_TEMPLATE` is unset the layer is simply absent and the
deterministic screen stands alone, which is the correct behaviour for a local
developer run rather than a silent downgrade in production.

See CLAUDE.md F-9.
"""
from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request

import google.auth
import google.auth.transport.requests

from ..config import get_settings
from ..core.untrusted import ScreenResult

log = logging.getLogger(__name__)

# Model Armor is served from regional `rep.googleapis.com` hosts. The global
# endpoint does not serve it, and the gcloud CLI targets a different host
# again — which is why `gcloud model-armor templates list` returns
# PERMISSION_DENIED on a project where the REST API works fine.
_ENDPOINT = "https://modelarmor.{location}.rep.googleapis.com/v1/{template}:sanitizeUserPrompt"

TIMEOUT_SECONDS = 8


def _access_token() -> str:
    credentials, _ = google.auth.default(
        scopes=["https://www.googleapis.com/auth/cloud-platform"]
    )
    credentials.refresh(google.auth.transport.requests.Request())
    return credentials.token


def is_configured() -> bool:
    return bool(get_settings().model_armor_template)


def screen_with_model_armor(text: str, *, location: str = "us-central1") -> ScreenResult:
    """Screen one string. Returns ScreenResult(safe=False) on any doubt.

    `MODEL_ARMOR_TEMPLATE` is the full resource name:
        projects/<p>/locations/<l>/templates/<t>
    """
    template = get_settings().model_armor_template
    if not template:
        # Not configured: this layer is absent, not passing. The caller has
        # already run the deterministic screen.
        return ScreenResult(safe=True)

    url = _ENDPOINT.format(location=location, template=template)
    body = json.dumps({"user_prompt_data": {"text": text}}).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Authorization": f"Bearer {_access_token()}",
            "Content-Type": "application/json",
        },
    )

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            payload = json.load(response)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        # Fail closed. Dropping a note costs personalization; passing an
        # unscreened one can put an unauthorised discount in a clinic's name.
        log.warning("Model Armor unreachable (%s); dropping the note", exc)
        return ScreenResult(safe=False, reason="armor_unavailable")

    result = payload.get("sanitizationResult", {})
    state = result.get("filterMatchState")
    if state == "MATCH_FOUND":
        matched = _matched_filters(result)
        return ScreenResult(safe=False, reason=f"armor_{matched}")
    if state == "NO_MATCH_FOUND":
        return ScreenResult(safe=True)

    # An unrecognised verdict is not a pass.
    log.warning("Model Armor returned an unexpected state %r; dropping the note", state)
    return ScreenResult(safe=False, reason="armor_unknown_verdict")


def _matched_filters(result: dict) -> str:
    """Which filter objected, for the audit trail."""
    names = []
    for group in (result.get("filterResults") or {}).values():
        for name, detail in (group or {}).items():
            if isinstance(detail, dict) and detail.get("matchState") == "MATCH_FOUND":
                names.append(name.replace("FilterResult", "").replace("Filter", ""))
    return "+".join(sorted(names)) or "match"
