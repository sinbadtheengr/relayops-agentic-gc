"""Record the return address and monitored contact printed on clinic messages."""

import argparse
import re

from sqlalchemy.orm import Session

from relayops_fleet.db.repo import build_engine, get_clinic


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clinic", required=True)
    parser.add_argument("--mailing-address", required=True)
    parser.add_argument("--contact-email", required=True)
    args = parser.parse_args()
    if not args.mailing_address.strip() or not re.fullmatch(
        r"[^@\s]+@[^@\s]+\.[^@\s]+", args.contact_email
    ):
        parser.error("A mailing address and monitored email are required")
    with Session(build_engine()) as session:
        clinic = get_clinic(session, args.clinic)
        clinic.mailing_address = args.mailing_address.strip()
        clinic.contact_email = args.contact_email.strip()
        session.commit()


if __name__ == "__main__":
    main()
