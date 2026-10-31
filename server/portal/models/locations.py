"""Lokaliteternes grænser og detaljer fra DOFbasen (docs/lokaliteter-plan.md, trin 2) og importen af dem.

Tabellerne ligger i observationsgruppen (dbgroups.OBS_TABLES) sammen med observationerne, de beskriver. Punktet i
locations.geom bruges fortsat overalt; grænsen er et lag ved siden af med historik (én række pr. version).
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import ARRAY, BigInteger, Boolean, DateTime, Index, Integer, SmallInteger, String, Text, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from portal.db import Base, Geometry


class LocationDetails(Base):
    """Oplysninger fra lokalitetssiden på DOFbasen (poplok.php) – én række pr. lokalitet."""

    __tablename__ = "location_details"

    loknr: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    lokid: Mapped[int | None] = mapped_column(Integer, index=True)
    navn: Mapped[str | None] = mapped_column(String(250))  # DOFbasens navn ved importen (locations.navn følger referencedata)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True)
    vis: Mapped[int | None] = mapped_column(SmallInteger)
    typer_tekst: Mapped[str | None] = mapped_column(String(300))  # som DOFbasen skriver det, fx "Sø/Permanent vandhul/mose"
    beskrivelse: Mapped[str | None] = mapped_column(Text)
    region: Mapped[str | None] = mapped_column(String(80))
    kommuner: Mapped[list[str]] = mapped_column(ARRAY(String(80)), default=list)
    afdelinger: Mapped[list[str]] = mapped_column(ARRAY(String(120)), default=list)
    iba_id: Mapped[int | None] = mapped_column(Integer)
    iba_navn: Mapped[str | None] = mapped_column(String(200))
    dofbasen: Mapped[dict | None] = mapped_column(JSONB)  # antal arter, observationsdage og observationer ved høsten
    felter: Mapped[dict | None] = mapped_column(JSONB)  # alle felter på siden, som de stod
    source: Mapped[str] = mapped_column(String(10), default="dofbasen")
    import_id: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LocationShape(Base):
    """En version af en lokalitets grænse. Den gældende er status='aktiv' og valid_to IS NULL; ældre versioner bevares,
    så en rettelse kan fortrydes, og så en ny DOFbasen-import kan se, om portalen har rettet grænsen siden."""

    __tablename__ = "location_shapes"
    __table_args__ = (
        Index("ix_location_shapes_current", "loknr", unique=True, postgresql_where=text("valid_to IS NULL AND status = 'aktiv'")),
        Index("ix_location_shapes_loknr", "loknr"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    loknr: Mapped[int] = mapped_column(Integer)
    geom = deferred(mapped_column(Geometry("MultiPolygon")))
    punkter: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(10))  # dofbasen / portal
    status: Mapped[str] = mapped_column(String(10), default="aktiv")  # aktiv / kladde / afvist
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dof_meta: Mapped[dict | None] = mapped_column(JSONB)  # DOFbasens oprettet/opdateret/accepteret
    import_id: Mapped[int | None] = mapped_column(Integer)
    user_id: Mapped[int | None] = mapped_column(Integer)  # brugeren ligger i den anden databasegruppe – ingen fremmednøgle
    note: Mapped[str | None] = mapped_column(String(500))


class LocationImport(Base):
    """En uploadet zip fra tools/dofbasen_lokaliteter/hent.py og dens gang: uploader → validerer → klar → anvender → anvendt
    (eller fejl). report er ændringsrapporten, som admin ser før Anvend."""

    __tablename__ = "location_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    size: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(12), default="uploader")
    manifest: Mapped[dict | None] = mapped_column(JSONB)
    report: Mapped[dict | None] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    uploaded_by: Mapped[int | None] = mapped_column(Integer)
    applied_by: Mapped[int | None] = mapped_column(Integer)
    applied: Mapped[dict | None] = mapped_column(JSONB)  # hvad Anvend gjorde (antal pr. gruppe)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LocationImportRow(Base):
    """Staging: én lokalitet fra en import, før den anvendes (slettes, når importen anvendes eller slettes)."""

    __tablename__ = "location_import_rows"

    import_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loknr: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    data: Mapped[dict] = mapped_column(JSONB)  # alt undtagen grænsen
    geom = deferred(mapped_column(Geometry("MultiPolygon")))
    punkter: Mapped[int] = mapped_column(Integer, default=0)
    dof_meta: Mapped[dict | None] = mapped_column(JSONB)
