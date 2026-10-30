"""Observationernes kategorier (SU > SUB > Bemærk > almindelig) og feedets filterniveauer."""

from __future__ import annotations

RANK: dict[str, int] = {"SU": 3, "SUB": 2, "bemaerk": 1, "alm": 0}
BY_RANK: dict[int, str] = {v: k for k, v in RANK.items()}
# Feedets niveauer: mindste rang for SU/SUB/Bemærk; de øvrige filtre ser på andet end kategorien
LEVELS: dict[str, int] = {"SU": 3, "SUB": 2, "bemaerk": 1, "all": 0, "local": 0, "pheno": 0}


def rank_sql(col: str = "o.kategori") -> str:
    """SQL-udtryk med kategoriens rang (0–3) for kolonnen col."""
    return f"CASE {col} WHEN 'SU' THEN 3 WHEN 'SUB' THEN 2 WHEN 'bemaerk' THEN 1 ELSE 0 END"


def rank(kategori: str | None) -> int:
    return RANK.get(kategori or "", 0)


def from_rank(r: int | None) -> str:
    return BY_RANK.get(r or 0, "alm")


def higher(a: str | None, b: str | None) -> str:
    """Den mest bemærkelsesværdige af to kategorier."""
    return a if rank(a) >= rank(b) else b  # type: ignore[return-value]


def at_least(level: str) -> list[str]:
    """Kategorierne der hører med til et SU/SUB/Bemærk-niveau (fx 'SUB' → SU og SUB)."""
    lo = LEVELS.get(level, 1)
    return [k for k, v in RANK.items() if v >= lo]
