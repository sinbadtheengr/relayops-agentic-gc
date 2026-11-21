"""Recipient-specific evidence. A clinic and RelayOps are distinct senders."""

import argparse
import re
from datetime import date
from urllib.parse import urlsplit

from sqlalchemy import text

from sqlalchemy.orm import Session
from relayops_fleet.core.gates import e164
from relayops_fleet.db.repo import build_engine, get_clinic

VALID = """
    clinic_id = :clinic_id AND event_date <= :day AND
    CASE basis
        WHEN 'express' THEN true
        WHEN 'publication' THEN unrestricted AND source_url IS NOT NULL AND role_relevance IS NOT NULL
        WHEN 'purchase' THEN :day < event_date + interval '2 years'
        WHEN 'inquiry' THEN :day < event_date + interval '6 months'
        ELSE false
    END
"""


def consent_id(session, clinic_id, destination, day):
    if not destination:
        return None
    return session.scalar(
        text(
            "SELECT id FROM marketing_consent WHERE destination=:recipient AND "
            + VALID
            + " ORDER BY recorded_at DESC, id DESC LIMIT 1"
        ),
        {"clinic_id": clinic_id, "recipient": destination.strip().lower(), "day": day},
    )


def record(
    session,
    clinic_id,
    destination,
    basis,
    event_date,
    evidence,
    source_url=None,
    role_relevance=None,
    unrestricted=False,
):
    destination = destination.strip().lower()
    if "@" in destination:
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", destination):
            raise ValueError("Invalid recipient email")
    else:
        destination = e164(destination)
    if not destination or event_date > date.today() or not evidence.strip():
        raise ValueError("A valid recipient, past or current event date, and evidence are required")
    if basis == "publication" and (
        not unrestricted
        or not (role_relevance or "").strip()
        or urlsplit(source_url or "").scheme not in {"http", "https"}
        or not urlsplit(source_url or "").hostname
    ):
        raise ValueError(
            "Publication requires a source URL, role relevance and no solicitation restriction"
        )
    session.execute(
        text("""
            INSERT INTO marketing_consent
            (clinic_id,destination,basis,event_date,evidence,source_url,role_relevance,unrestricted)
            VALUES (:clinic_id,:destination,:basis,:event_date,:evidence,:source_url,:role_relevance,:unrestricted)
        """),
        dict(
            clinic_id=clinic_id,
            destination=destination,
            basis=basis,
            event_date=event_date,
            evidence=evidence,
            source_url=source_url,
            role_relevance=role_relevance,
            unrestricted=unrestricted,
        ),
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clinic", required=True, help="Sending clinic name")
    parser.add_argument("--destination", required=True)
    parser.add_argument(
        "--basis", required=True, choices=["express", "purchase", "inquiry", "publication"]
    )
    parser.add_argument("--event-date", required=True, type=date.fromisoformat)
    parser.add_argument("--evidence", required=True, help="Reference to the original record")
    parser.add_argument("--source-url")
    parser.add_argument("--role-relevance")
    parser.add_argument(
        "--unrestricted",
        action="store_true",
        help="Verified no statement against unsolicited messages",
    )
    args = vars(parser.parse_args())
    clinic = args.pop("clinic")
    with Session(build_engine()) as session:
        record(session, get_clinic(session, clinic).id, **args)
        session.commit()


if __name__ == "__main__":
    main()
