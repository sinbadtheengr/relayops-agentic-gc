"""Clinic export ingestion. NO LLM CALLS IN THIS MODULE.

Jane, Boulevard, Vagaro, Mindbody and Fresha all name the same six facts
differently and some split the name across two columns, so headers are matched
by synonym rather than hard-coded.

**Nothing is silently defaulted**, because segmentation targets on these
fields: a blanked spend makes a VIP look ordinary and a blanked date makes
everyone look maximally lapsed. A row missing a name, phone or readable
last-visit date is **skipped with a reason**; unreadable counts and spend
become `None`, never `0`. A missing *required column* raises rather than
guessing.

The skip report is returned on every run, including clean ones. Rows it
skipped are clients who will never be contacted, and silence there would look
identical to a clean import.

Ported from relayops-prod `src/relayops/pipeline/client_import.py` — see
CLAUDE.md F-3.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .gates import e164, normalize_email

SYNONYMS: dict[str, tuple[str, ...]] = {
    "name": (
        "clientname", "patientname", "customername", "fullname", "name",
        "client", "patient", "customer",
    ),
    "phone": (
        "mobilephone", "mobilenumber", "cellphone", "mobile", "cell",
        "phonenumber", "phone", "primaryphone", "homephone", "telephone",
    ),
    "email": ("emailaddress", "email", "emailaddr", "clientemail", "patientemail"),
    "last_visit": (
        "lastvisitdate", "lastvisit", "lastappointment", "lastappointmentdate",
        "lastappt", "lastseen", "mostrecentvisit", "lastbooking", "lastservicedate",
    ),
    "visit_count": (
        "totalvisits", "visitcount", "numberofvisits", "appointments",
        "totalappointments", "appointmentcount", "visits", "bookings",
        # "# of Visits" (Jane) normalizes to "ofvisits" once the # is stripped.
        # It matched nothing and silently dropped the count.
        "ofvisits", "ofappointments", "numvisits", "visitstotal", "novisits",
        "noofvisits", "noofappointments",
    ),
    "lifetime_spend": (
        "lifetimespend", "lifetimevalue", "totalspend", "totalspent", "totalsales",
        "totalrevenue", "lifetimerevenue", "ltv", "revenue", "spend",
    ),
    "last_service": (
        "lastservice", "service", "treatment", "lasttreatment", "servicename",
        "appointmenttype", "lastappointmenttype",
    ),
    "notes": ("notes", "note", "clientnotes", "comments", "remarks"),
}

FIRST_NAME = ("firstname", "givenname", "clientfirstname", "patientfirstname", "first")
LAST_NAME = ("lastname", "surname", "familyname", "clientlastname", "patientlastname", "last")

REQUIRED = ("name", "phone", "last_visit")

_DATE_FORMATS = ("%Y-%m-%d", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%b %d, %Y", "%Y-%m-%dT%H:%M:%S")
_SLASHED = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{2,4})$")


class UnmappableExport(ValueError):
    """A required column is absent. Raised rather than guessed at."""


def normalize(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


@dataclass
class ColumnMapping:
    """Which source column supplies each fact. Pass one to override detection."""

    name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    last_visit: str | None = None
    visit_count: str | None = None
    lifetime_spend: str | None = None
    last_service: str | None = None
    notes: str | None = None

    def has_name(self) -> bool:
        return bool(self.name or self.first_name)


@dataclass
class SkippedRow:
    line: int
    reason: str
    raw_name: str = ""


@dataclass
class ImportReport:
    """What was read, and — more importantly — what was not."""

    total_rows: int = 0
    imported: int = 0
    skipped: list[SkippedRow] = field(default_factory=list)
    slashed_dates_ambiguous: bool = False
    slashed_format: str | None = None
    mapping: ColumnMapping | None = None

    def render(self) -> str:
        lines = [f"{self.imported} of {self.total_rows} row(s) imported"]
        if self.slashed_dates_ambiguous:
            lines.append(
                "  WARNING: slashed dates never resolved d/m/y vs m/d/y in this file. "
                f"Assumed {self.slashed_format}. Confirm with the clinic before campaigning."
            )
        if self.skipped:
            lines.append(f"  {len(self.skipped)} row(s) skipped — these clients will NOT "
                         "be contacted:")
            for row in self.skipped:
                who = f" ({row.raw_name})" if row.raw_name else ""
                lines.append(f"    line {row.line}{who}: {row.reason}")
        else:
            lines.append("  no rows skipped")
        return "\n".join(lines)


def detect_mapping(headers: list[str]) -> ColumnMapping:
    """Match headers to facts by synonym. Raises when a required one is absent."""
    by_norm = {normalize(h): h for h in headers}
    mapping = ColumnMapping()

    for field_name, options in SYNONYMS.items():
        for candidate in options:
            if candidate in by_norm:
                setattr(mapping, field_name, by_norm[candidate])
                break

    if not mapping.name:  # a split name is common; try that before giving up
        for candidate in FIRST_NAME:
            if candidate in by_norm:
                mapping.first_name = by_norm[candidate]
                break
        for candidate in LAST_NAME:
            if candidate in by_norm:
                mapping.last_name = by_norm[candidate]
                break

    missing = [
        f for f in REQUIRED if not (mapping.has_name() if f == "name" else getattr(mapping, f))
    ]
    if missing:
        raise UnmappableExport(
            f"export is missing required column(s): {', '.join(missing)}. "
            f"Headers present: {', '.join(headers)}. "
            "Pass an explicit ColumnMapping to override."
        )
    return mapping


def detect_slashed_order(values: list[str]) -> tuple[str, bool]:
    """Return (strptime format, ambiguous) for d/m/y vs m/d/y.

    Decided from the COLUMN, not per value: a first part above 12 can only be
    a day, a second part above 12 can only be a month. One unambiguous row
    settles the whole file. If none ever appears the file is genuinely
    ambiguous, and the report says so rather than the caller assuming.
    """
    for raw in values:
        match = _SLASHED.match((raw or "").strip())
        if not match:
            continue
        first, second = int(match.group(1)), int(match.group(2))
        if first > 12:
            return "%d/%m/%Y", False
        if second > 12:
            return "%m/%d/%Y", False
    return "%m/%d/%Y", True


def parse_date(raw: str, slashed_format: str) -> date | None:
    """A real date, or None when the value cannot be read."""
    value = (raw or "").strip()
    if not value:
        return None
    if _SLASHED.match(value):
        parts = value.split("/")
        if len(parts[2]) == 2:
            value = f"{parts[0]}/{parts[1]}/20{parts[2]}"
        try:
            # Naive on purpose: a last-visit date is a calendar date in the
            # clinic's own reckoning. Attaching a timezone would invent
            # precision the export does not have.
            return datetime.strptime(value, slashed_format).date()  # noqa: DTZ007
        except ValueError:
            return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(value[: len(fmt) + 6], fmt).date()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def parse_money_cents(raw: str) -> int | None:
    """'$1,234.56' -> 123456. None when unreadable, NEVER 0 as a fallback.

    Zero would make a high-spending client look ordinary and drag the clinic's
    VIP cutoff down with them.
    """
    value = re.sub(r"[^0-9.\-]", "", (raw or "").strip())
    if not value or value in ("-", ".", "-."):
        return None
    try:
        return round(float(value) * 100)
    except ValueError:
        return None


def parse_count(raw: str) -> int | None:
    value = re.sub(r"[^0-9\-]", "", (raw or "").strip())
    if not value or value == "-":
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _row_name(row: dict[str, str], mapping: ColumnMapping) -> str:
    if mapping.name:
        return (row.get(mapping.name) or "").strip()
    parts = [
        (row.get(mapping.first_name) or "").strip() if mapping.first_name else "",
        (row.get(mapping.last_name) or "").strip() if mapping.last_name else "",
    ]
    return " ".join(p for p in parts if p).strip()


def load_client_csv(
    path: Path, mapping: ColumnMapping | None = None
) -> tuple[list[dict[str, Any]], ImportReport]:
    """Read a clinic export into client-shaped dicts, plus a report.

    The returned dicts carry `client_key` already normalized to E.164, so the
    caller never writes an unnormalized key and two spellings of one number
    cannot become two clients.
    """
    with Path(path).open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
        headers = list(rows[0].keys()) if rows else []

    mapping = mapping or detect_mapping(headers)
    report = ImportReport(total_rows=len(rows), mapping=mapping)

    slashed_format, ambiguous = detect_slashed_order(
        [(r.get(mapping.last_visit) or "") for r in rows]
    )
    report.slashed_format = slashed_format
    report.slashed_dates_ambiguous = ambiguous

    records: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=2):  # line 1 is the header
        raw_name = _row_name(row, mapping)
        if not raw_name:
            report.skipped.append(SkippedRow(index, "no client name"))
            continue

        phone = e164(row.get(mapping.phone) if mapping.phone else None)
        if phone is None:
            report.skipped.append(
                SkippedRow(index, "phone missing or unreadable", raw_name)
            )
            continue

        last_visit = parse_date(row.get(mapping.last_visit, ""), slashed_format)
        if last_visit is None:
            report.skipped.append(
                SkippedRow(index, "last-visit date missing or unreadable", raw_name)
            )
            continue

        records.append(
            {
                "client_key": phone,
                "first_name": raw_name.split()[0],
                "email": normalize_email(row.get(mapping.email) if mapping.email else None),
                "last_visit": last_visit,
                # None, never 0 — see parse_money_cents.
                "visit_count": parse_count(row.get(mapping.visit_count, ""))
                if mapping.visit_count
                else None,
                "lifetime_spend_cents": parse_money_cents(row.get(mapping.lifetime_spend, ""))
                if mapping.lifetime_spend
                else None,
                "last_service": (row.get(mapping.last_service) or "").strip() or None
                if mapping.last_service
                else None,
                "notes": (row.get(mapping.notes) or "").strip() or None
                if mapping.notes
                else None,
            }
        )

    report.imported = len(records)
    return records, report
