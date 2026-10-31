"""Referencedata: arter, kategorier, afdelinger, kommuner, lokaliteter og koder."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from portal.db import Base, Geometry


class Species(Base):
    __tablename__ = "species"

    artnr: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    navn: Mapped[str] = mapped_column(String(200), index=True)
    latin: Mapped[str | None] = mapped_column(String(200))
    sortering: Mapped[int | None] = mapped_column(Integer)
    # SU / SUB / Alm – fra DOFbasens artdata/sudata/subdata
    klass: Mapped[str] = mapped_column(String(8), default="Alm")
    # art / hybrid / ub (ubestemt) / andre / underart
    art_type: Mapped[str] = mapped_column(String(16), default="art")
    # Art, der tælles på årslister (underarter → nominatart). None = tælles ikke.
    list_artnr: Mapped[int | None] = mapped_column(Integer)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)


class SpeciesCategory(Base):
    """Listekategori (A–F) for en art fra en given dato. Kun A, B og C er krydsbare på årslister.
    Rækker slettes aldrig – en fjernet kategori får removed_at, så historikken bevares."""

    __tablename__ = "species_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, index=True)
    kategori: Mapped[str] = mapped_column(String(1))
    valid_from: Mapped[date | None] = mapped_column(Date)  # None = altid
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)  # users.id – ingen fremmednøgle (observationsgruppen)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_by: Mapped[int | None] = mapped_column(Integer)  # users.id – ingen fremmednøgle (observationsgruppen)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CategoryOverride(Base):
    """Kategori for en serie af observationer af en art (listeart) på én lokalitet i et datointerval,
    fx et godkendt kategori A-fund af en art der ellers er kategori D. Går forud for artens kategori."""

    __tablename__ = "category_overrides"
    __table_args__ = (Index("ix_catov_art_lok", "artnr", "loknr"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer)
    loknr: Mapped[int] = mapped_column(Integer)
    date_from: Mapped[date] = mapped_column(Date)
    date_to: Mapped[date] = mapped_column(Date)
    kategori: Mapped[str] = mapped_column(String(1))
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)  # users.id – ingen fremmednøgle (observationsgruppen)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_by: Mapped[int | None] = mapped_column(Integer)  # users.id – ingen fremmednøgle (observationsgruppen)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ManualFind(Base):
    """Fund lagt ind af en administrator, som ikke står i DOFbasen (fx godkendte fund til havs). Spejles ind i
    observations med obsid = -id, så det tæller i Arter pr. år, på lokaliteten og på artens side – men ikke på nogens
    personlige liste (ingen obserkode). Synkroniseringen fra DOFbasen rører aldrig negative obsid'er."""

    __tablename__ = "manual_finds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dato: Mapped[date] = mapped_column(Date)
    artnr: Mapped[int] = mapped_column(Integer)
    loknr: Mapped[int | None] = mapped_column(Integer)
    sted: Mapped[str | None] = mapped_column(String(200))
    antal: Mapped[int] = mapped_column(Integer, default=1)
    note: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(Integer)  # users.id – ingen fremmednøgle (observationsgruppen)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_by: Mapped[int | None] = mapped_column(Integer)  # users.id – ingen fremmednøgle (observationsgruppen)
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Afdeling(Base):
    __tablename__ = "afdelinger"

    id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    navn: Mapped[str] = mapped_column(String(80), unique=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)


class Kommune(Base):
    __tablename__ = "kommuner"

    kode: Mapped[str] = mapped_column(String(4), primary_key=True)
    navn: Mapped[str] = mapped_column(String(80), index=True)
    dof_id: Mapped[int | None] = mapped_column(Integer)
    geom = deferred(mapped_column(Geometry("MultiPolygon")))
    geom_simple = deferred(mapped_column(Geometry("MultiPolygon")))


class Location(Base):
    __tablename__ = "locations"

    loknr: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    navn: Mapped[str] = mapped_column(String(250))
    afdeling_id: Mapped[int | None] = mapped_column(SmallInteger, index=True)
    kommune_kode: Mapped[str | None] = mapped_column(String(4), index=True)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    geom = deferred(mapped_column(Geometry("Point")))
    # Admin → Lokaliteter (services/location_edit.py): lokalitet inden i en anden, hvem der har oprettet den (dofbasen/portal)
    # og – for en sammenlagt lokalitet – den, der lever videre. Observationer bliver altid på deres loknr.
    parent_loknr: Mapped[int | None] = mapped_column(Integer, index=True)
    origin: Mapped[str] = mapped_column(String(10), default="dofbasen", server_default="dofbasen")
    merged_into: Mapped[int | None] = mapped_column(Integer)


class BemaerkThreshold(Base):
    __tablename__ = "bemaerk_thresholds"

    afdeling_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    artnavn: Mapped[str] = mapped_column(String(200), primary_key=True)
    antal: Mapped[int] = mapped_column(Integer)


class PhenologyWindow(Base):
    """DOFbasens 'bemærk uden for perioden'-vinduer (sommer-/vinterfugle)."""

    __tablename__ = "phenology_windows"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    artnavn: Mapped[str] = mapped_column(String(200), index=True)
    fra: Mapped[str] = mapped_column(String(5))  # DD-MM
    til: Mapped[str] = mapped_column(String(5))


class ReferenceEdit(Base):
    """Ændringer fra Moderation → Arter i bemærk-tærskler (pr. lokalafdeling) og fænologivinduer. De lægges oven på DOFbasens
    værdier hver gang referencedata hentes, så de overlever opdateringen. dof_* er DOFbasens værdi (til fortryd)."""

    __tablename__ = "reference_edits"
    __table_args__ = (UniqueConstraint("kind", "afdeling_id", "artnavn"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # bemaerk / pheno
    afdeling_id: Mapped[int] = mapped_column(SmallInteger, default=0)  # 0 for fænologi (gælder hele landet)
    artnavn: Mapped[str] = mapped_column(String(200))
    antal: Mapped[int | None] = mapped_column(Integer)  # bemaerk
    windows: Mapped[list | None] = mapped_column(JSONB)  # pheno: [["DD-MM", "DD-MM"], …]
    removed: Mapped[bool] = mapped_column(Boolean, default=False)
    dof_antal: Mapped[int | None] = mapped_column(Integer)
    dof_windows: Mapped[list | None] = mapped_column(JSONB)
    user_id: Mapped[int | None] = mapped_column(Integer)  # brugeren ligger i den anden databasegruppe – ingen fremmednøgle
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SpeciesEdit(Base):
    """Ændringer af en art fra Moderation → Arter (navn, latin, sortering, status, type, «tæller som», udeluk). fields er de
    felter, portalen har ændret; de lægges oven på DOFbasens værdier hver gang referencedata hentes (dof/reference.py →
    apply_species_edits). dof er artens værdier før første ændring (til fortryd)."""

    __tablename__ = "species_edits"

    artnr: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    fields: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list)
    navn: Mapped[str | None] = mapped_column(String(200))
    latin: Mapped[str | None] = mapped_column(String(200))
    sortering: Mapped[int | None] = mapped_column(Integer)
    klass: Mapped[str | None] = mapped_column(String(8))
    art_type: Mapped[str | None] = mapped_column(String(16))
    list_artnr: Mapped[int | None] = mapped_column(Integer)
    excluded: Mapped[bool | None] = mapped_column(Boolean)
    dof: Mapped[dict] = mapped_column(JSONB, default=dict)
    user_id: Mapped[int | None] = mapped_column(Integer)  # brugeren ligger i den anden databasegruppe – ingen fremmednøgle
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CodeText(Base):
    """Opslag for adfærds-/dragt-/alderskoder → beskrivelse."""

    __tablename__ = "code_texts"

    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(String(120))
