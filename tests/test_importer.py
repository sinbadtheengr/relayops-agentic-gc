"""Clinic export ingestion. Pure functions over files — no database, no model.

The rules being tested are all about refusing to guess. Every silent default
this module could take would corrupt targeting downstream: a blanked spend
mis-tiers a VIP, a blanked date makes someone look maximally lapsed, and a
dropped row is a client who is simply never contacted.

Acceptance criteria for F-3 (see CLAUDE.md).
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from relayops_fleet.core.importer import (
    ColumnMapping,
    UnmappableExport,
    detect_mapping,
    detect_slashed_order,
    load_client_csv,
    parse_count,
    parse_date,
    parse_money_cents,
)


def write_csv(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "export.csv"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return path


# --- Header detection across booking systems ------------------------------


@pytest.mark.parametrize(
    "headers",
    [
        "Client Name,Mobile Phone,Last Visit Date",          # Jane-ish
        "Customer,Cell,Last Appointment",                    # Vagaro-ish
        "Full Name,Phone Number,Most Recent Visit",          # Mindbody-ish
        "patient name,telephone,last seen",                  # lowercase, spaced
        "CLIENTNAME,PHONE,LASTVISIT",                        # shouting
    ],
)
def test_the_same_six_facts_are_found_whatever_they_are_called(headers: str) -> None:
    mapping = detect_mapping(headers.split(","))
    assert mapping.has_name()
    assert mapping.phone and mapping.last_visit


def test_a_split_name_is_recognised() -> None:
    mapping = detect_mapping(["First Name", "Last Name", "Mobile", "Last Visit"])
    assert not mapping.name
    assert mapping.first_name and mapping.last_name
    assert mapping.has_name()


def test_jane_hash_of_visits_header_is_matched() -> None:
    """"# of Visits" normalizes to "ofvisits". It matched nothing once and
    silently dropped the visit count for a whole clinic."""
    mapping = detect_mapping(["Client Name", "Phone", "Last Visit", "# of Visits"])
    assert mapping.visit_count == "# of Visits"


def test_a_missing_required_column_raises_rather_than_guessing() -> None:
    with pytest.raises(UnmappableExport) as exc:
        detect_mapping(["Client Name", "Email"])
    assert "phone" in str(exc.value)
    assert "last_visit" in str(exc.value)


def test_an_explicit_mapping_overrides_detection(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "who,digits,seen\nDana Q,416-555-0142,2026-01-03")
    mapping = ColumnMapping(name="who", phone="digits", last_visit="seen")
    records, report = load_client_csv(path, mapping)
    assert report.imported == 1
    assert records[0]["client_key"] == "+14165550142"


# --- Slashed-date disambiguation ------------------------------------------


def test_a_day_above_12_settles_the_whole_column() -> None:
    fmt, ambiguous = detect_slashed_order(["03/04/2026", "25/12/2025"])
    assert fmt == "%d/%m/%Y"
    assert not ambiguous


def test_a_month_above_12_settles_it_the_other_way() -> None:
    fmt, ambiguous = detect_slashed_order(["03/04/2026", "12/25/2025"])
    assert fmt == "%m/%d/%Y"
    assert not ambiguous


def test_a_genuinely_ambiguous_file_is_flagged_not_assumed_silently(tmp_path: Path) -> None:
    """Every date under 13 in both positions. The caller must be told."""
    path = write_csv(
        tmp_path,
        "Client Name,Phone,Last Visit\n"
        "Dana Q,416-555-0142,03/04/2026\n"
        "Priya R,416-555-0143,05/06/2026",
    )
    _records, report = load_client_csv(path)
    assert report.slashed_dates_ambiguous
    assert "never resolved" in report.render()


def test_two_digit_years_are_expanded() -> None:
    assert parse_date("25/12/25", "%d/%m/%Y") == date(2025, 12, 25)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("2026-01-03", date(2026, 1, 3)),
        ("2026/01/03", date(2026, 1, 3)),
        ("03-Jan-2026", date(2026, 1, 3)),
        ("Jan 3, 2026", date(2026, 1, 3)),
        ("2026-01-03T14:30:00", date(2026, 1, 3)),
    ],
)
def test_common_date_formats_are_read(raw: str, expected: date) -> None:
    assert parse_date(raw, "%m/%d/%Y") == expected


@pytest.mark.parametrize("raw", ["", "   ", "not a date", "31/31/2026"])
def test_unreadable_dates_return_none(raw: str) -> None:
    assert parse_date(raw, "%m/%d/%Y") is None


# --- None, never zero -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("$1,234.56", 123456), ("1234.56", 123456), ("0", 0), ("$0.00", 0)],
)
def test_money_parses_to_cents(raw: str, expected: int) -> None:
    assert parse_money_cents(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "n/a", "-", "unknown"])
def test_unreadable_money_is_none_never_zero(raw: str) -> None:
    """Zero would make a high-spending client look ordinary and drag the
    clinic's VIP cutoff down with them."""
    assert parse_money_cents(raw) is None


@pytest.mark.parametrize("raw", ["", "n/a", "-", "many"])
def test_unreadable_counts_are_none_never_zero(raw: str) -> None:
    assert parse_count(raw) is None


# --- Skip-with-a-reason ---------------------------------------------------


def test_one_row_per_failure_mode_yields_one_skip_line_each(tmp_path: Path) -> None:
    """The acceptance criterion. Each skipped row must name its own reason."""
    path = write_csv(
        tmp_path,
        "Client Name,Phone,Last Visit,Total Spend\n"
        "Dana Q,416-555-0142,2026-01-03,$4120\n"     # fine
        ",416-555-0143,2026-01-04,$300\n"            # no name
        "Priya R,,2026-01-05,$300\n"                 # no phone
        "Marcus T,not-a-phone,2026-01-06,$300\n"     # unreadable phone
        "Elena V,416-555-0146,,$300\n"               # no date
        "Tomas W,416-555-0147,gibberish,$300",       # unreadable date
    )
    records, report = load_client_csv(path)

    assert len(records) == 1
    assert report.total_rows == 6
    assert report.imported == 1
    assert len(report.skipped) == 5

    reasons = [s.reason for s in report.skipped]
    assert reasons.count("no client name") == 1
    assert reasons.count("phone missing or unreadable") == 2
    assert reasons.count("last-visit date missing or unreadable") == 2

    text = report.render()
    for skipped in report.skipped:
        assert f"line {skipped.line}" in text
    assert "will NOT be contacted" in text


def test_the_report_is_rendered_even_when_nothing_was_skipped(tmp_path: Path) -> None:
    """Silence would look identical to a clean import."""
    path = write_csv(tmp_path, "Client Name,Phone,Last Visit\nDana Q,416-555-0142,2026-01-03")
    _records, report = load_client_csv(path)
    assert not report.skipped
    assert "no rows skipped" in report.render()


def test_a_skipped_row_names_the_client_where_it_can(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "Client Name,Phone,Last Visit\nPriya R,,2026-01-05")
    _records, report = load_client_csv(path)
    assert report.skipped[0].raw_name == "Priya R"
    assert "Priya R" in report.render()


# --- The records themselves -----------------------------------------------


def test_phone_is_normalized_so_two_spellings_are_one_client(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "Client Name,Phone,Last Visit\n"
        "Dana Q,(416) 555-0142,2026-01-03\n"
        "Dana Q,4165550142,2026-01-04",
    )
    records, _report = load_client_csv(path)
    assert {r["client_key"] for r in records} == {"+14165550142"}


def test_optional_columns_absent_entirely_are_none_not_zero(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "Client Name,Phone,Last Visit\nDana Q,416-555-0142,2026-01-03")
    records, _report = load_client_csv(path)
    assert records[0]["lifetime_spend_cents"] is None
    assert records[0]["visit_count"] is None
    assert records[0]["notes"] is None


def test_a_full_export_maps_every_fact(tmp_path: Path) -> None:
    path = write_csv(
        tmp_path,
        "Client Name,Mobile Phone,Email,Last Visit Date,# of Visits,Lifetime Spend,"
        "Last Service,Notes\n"
        "Dana Quinn,(416) 555-0142,Dana@Example.COM,2026-01-03,7,\"$4,120.00\","
        "injectables,Prefers afternoons",
    )
    records, report = load_client_csv(path)
    assert report.imported == 1
    record = records[0]
    assert record["client_key"] == "+14165550142"
    assert record["first_name"] == "Dana"
    assert record["email"] == "dana@example.com"
    assert record["last_visit"] == date(2026, 1, 3)
    assert record["visit_count"] == 7
    assert record["lifetime_spend_cents"] == 412000
    assert record["last_service"] == "injectables"
    assert record["notes"] == "Prefers afternoons"


def test_an_empty_export_is_not_a_crash(tmp_path: Path) -> None:
    path = write_csv(tmp_path, "Client Name,Phone,Last Visit")
    records, report = load_client_csv(path, ColumnMapping(
        name="Client Name", phone="Phone", last_visit="Last Visit"
    ))
    assert records == []
    assert report.total_rows == 0
