"""Adfærdskoder fra DOFbasen og hvornår en fugl regnes som trækkende.

Der er to definitioner, fordi de svarer på to forskellige spørgsmål:

* ``FIRST_MIGRATION`` – *første* led er træk (T, TF, otte retninger). Bruges af fænologien, trækkortet, kalenderne og
  parseren: "talt som trækkende på trækstedet", også trækforsøg.
* ``MIGRATING`` – *sidste* led er træk (16 retninger, T, TU, OF; ikke TF). Bruges af fund-estimatet og lokale hits:
  "er fuglen videre?" – "R SV" (rastede, trak så mod SV) er videre, "N R" (trak ind og rastede) er blevet.
"""

from __future__ import annotations

DIRS: dict[str, float] = {
    "N": 0.0, "NNØ": 22.5, "NØ": 45.0, "ØNØ": 67.5, "Ø": 90.0, "ØSØ": 112.5, "SØ": 135.0, "SSØ": 157.5,
    "S": 180.0, "SSV": 202.5, "SV": 225.0, "VSV": 247.5, "V": 270.0, "VNV": 292.5, "NV": 315.0, "NNV": 337.5}
MIGRATING: tuple[str, ...] = (*DIRS, "T", "TU", "OF")
FIRST_MIGRATION: tuple[str, ...] = ("T", "TF", "N", "NØ", "Ø", "SØ", "S", "SV", "V", "NV")


def _parts(adfkode: str | None) -> list[str]:
    return [p for p in (adfkode or "").split() if p != "HØ"]  # "T HØ" = trækkende, kun hørt


def migration(adfkode: str | None) -> tuple[bool, float | None]:
    """(videre på træk, retning i grader eller None) ud fra adfærdens sidste led."""
    parts = _parts(adfkode)
    last = parts[-1] if parts else None
    return last in MIGRATING, DIRS.get(last or "")


def is_migrating(adfkode: str | None) -> bool:
    """Er fuglen videre på træk (sidste led)?"""
    return migration(adfkode)[0]


def first_is_migration(adfkode: str | None) -> bool:
    """Er første led træk (fænologiens og trækkortets definition)?"""
    parts = (adfkode or "").split()
    return bool(parts) and parts[0] in FIRST_MIGRATION


def first_migration_sql(col: str = "adfkode") -> str:
    """SQL: første led af kolonnen er træk (som first_is_migration)."""
    codes = ",".join(f"'{c}'" for c in FIRST_MIGRATION)
    return f"split_part(coalesce({col}, ''), ' ', 1) IN ({codes})"


def last_migration_sql(col: str = "o.adfkode", param: str = "mig") -> str:
    """SQL: sidste led uden "HØ" er træk (som is_migrating). Kræver parameteren :param = list(MIGRATING)."""
    return (rf"coalesce(substring(regexp_replace(coalesce({col}, ''), '(\s+HØ)+\s*$', '') from '(\S+)\s*$') "
            f"= ANY(CAST(:{param} AS text[])), false)")
