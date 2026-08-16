"""Load a clinic's booking-software export into one tenant.

    python scripts/import_clinic_export.py "Glow Aesthetics (demo)" export.csv

The clinic must already be registered — `get_clinic` refuses to create on a
miss, so a typo in the name cannot silently become a second tenant holding
half of one clinic's clients.

The skip report always prints. Rows it skipped are clients who will never be
contacted, and silence there would look identical to a clean import.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from relayops_fleet.config import get_settings
from relayops_fleet.core.importer import UnmappableExport, load_client_csv
from relayops_fleet.db import campaign_repo, repo


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("clinic", help="exact registered clinic name")
    ap.add_argument("csv_path", type=Path)
    ap.add_argument("--dry-run", action="store_true", help="report only, write nothing")
    args = ap.parse_args()

    try:
        records, report = load_client_csv(args.csv_path)
    except UnmappableExport as exc:
        print(f"FAILED: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc

    print(report.render())

    if args.dry_run:
        print("\ndry run — nothing written")
        return

    engine = repo.build_engine(get_settings().database_url)
    Session = repo.build_sessionmaker(engine)
    with Session() as session, session.begin():
        clinic = repo.get_clinic(session, args.clinic)
        inserted, updated = campaign_repo.upsert_clients(
            session, clinic_id=clinic.id, records=records
        )
    engine.dispose()
    print(f"\n{clinic.name}: {inserted} new, {updated} updated")


if __name__ == "__main__":
    main()
