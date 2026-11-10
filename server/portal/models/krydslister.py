"""Krydslister-modulet: World Bird List (kl_taxa), artslister med kategorier og sjældenhed, redigerbare listetyper, manuelle kryds og automatiske kryds.

Alt er brugerdata (dbgroups.USER_TABLES). De automatiske kryds beregnes ud fra observationerne i en separat forespørgsel (aldrig i samme SQL)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class KlTaxon(Base):
    """En fuglekategori i World Bird List (art eller underart). artnr kobler til DOFbasens art via latinsk navn."""

    __tablename__ = "kl_taxa"
    __table_args__ = (UniqueConstraint("version", "latin"), Index("ix_kl_taxa_artnr", "artnr"), Index("ix_kl_taxa_sort", "sort"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    rang: Mapped[str] = mapped_column(String(8), default="art")  # art / underart
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("kl_taxa.id", ondelete="SET NULL"))
    orden: Mapped[str | None] = mapped_column(String(80))
    familie: Mapped[str | None] = mapped_column(String(120))
    latin: Mapped[str] = mapped_column(String(200))
    engelsk: Mapped[str | None] = mapped_column(String(200))
    dansk: Mapped[str | None] = mapped_column(String(200))
    netfugl_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    avilist_id: Mapped[str | None] = mapped_column(String(40), index=True)
    artnr: Mapped[int | None] = mapped_column(Integer)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    version: Mapped[str] = mapped_column(String(40), default="netfugl")


class KlSpeciesList(Base):
    __tablename__ = "kl_species_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(24), unique=True)  # dk / vp …
    navn: Mapped[str] = mapped_column(String(120))
    beskrivelse: Mapped[str | None] = mapped_column(Text)
    sort: Mapped[int] = mapped_column(Integer, default=0)


class KlSpeciesListEntry(Base):
    """kategorier: A–F (flere pr. art, fx ['A','C']); sjaeldenhed: almindelig / relativ_almindelig / ualmindelig / sjaelden / su."""

    __tablename__ = "kl_species_list_entries"
    __table_args__ = (Index("ix_kl_entries_taxon", "taxon_id"),)

    list_id: Mapped[int] = mapped_column(ForeignKey("kl_species_lists.id", ondelete="CASCADE"), primary_key=True)
    taxon_id: Mapped[int] = mapped_column(ForeignKey("kl_taxa.id", ondelete="CASCADE"), primary_key=True)
    kategorier: Mapped[list[str]] = mapped_column(ARRAY(String(1)), default=list, server_default="{}")
    sjaeldenhed: Mapped[str] = mapped_column(String(20), default="almindelig")
    sort: Mapped[int] = mapped_column(Integer, default=0)


class KlListDef(Base):
    """En listetype (erstatter showcasens hårdkodede LISTS). navn kan indeholde {p} (år, måned, område).
    regler: {kategorier: ['A','B','C'], godkendelse: bool, kun_ringmaerkning: bool, land: 'DK'}."""

    __tablename__ = "kl_list_defs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(String(24), unique=True)
    navn: Mapped[str] = mapped_column(String(160))
    ref_list_id: Mapped[int | None] = mapped_column(ForeignKey("kl_species_lists.id", ondelete="SET NULL"))
    auto: Mapped[bool] = mapped_column(Boolean, default=False)
    manuel: Mapped[bool] = mapped_column(Boolean, default=False)
    param: Mapped[str | None] = mapped_column(String(10))  # year / month / afdeling / kommune
    regler: Mapped[dict] = mapped_column(JSONB, default=dict)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    beskrivelse_md: Mapped[str | None] = mapped_column(Text)


class KlKryds(Base):
    """Manuelt kryds (supplerer de automatiske). hidden=True skjuler et automatisk kryds. status: ok / afventer / afvist (lister med regler.godkendelse)."""

    __tablename__ = "kl_kryds"
    __table_args__ = (Index("ix_kl_kryds_list", "liste", "obserkode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    obserkode: Mapped[str] = mapped_column(String(16), index=True)
    liste: Mapped[str] = mapped_column(String(24))
    taxon_id: Mapped[int | None] = mapped_column(ForeignKey("kl_taxa.id", ondelete="SET NULL"))
    artnr: Mapped[int | None] = mapped_column(Integer)
    latin: Mapped[str] = mapped_column(String(200))
    navn: Mapped[str] = mapped_column(String(200))
    dato: Mapped[date | None] = mapped_column(Date)
    lokalitet: Mapped[str | None] = mapped_column(String(250))
    land: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(String(500))
    obsid: Mapped[int | None] = mapped_column(BigInteger)  # observationen krydset bygger på (ædelarter markeret fra en årsliste)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    status: Mapped[str] = mapped_column(String(10), default="ok", server_default="ok")
    behandlet_af: Mapped[int | None] = mapped_column(Integer)
    kilde: Mapped[str] = mapped_column(String(10), default="manuel", server_default="manuel")  # manuel / netfugl (en senere import af Netfugls kryds)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class KlAutoList(Base):
    """Automatiske kryds: én række pr. deltager og liste med parallelle arrays (som nf_auto_lists)."""

    __tablename__ = "kl_auto_lists"

    obserkode: Mapped[str] = mapped_column(String(16), primary_key=True)
    liste: Mapped[str] = mapped_column(String(24), primary_key=True)  # dk / aar:2026 / md:1 / afd:574 / kom:0101
    artnr: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    dato: Mapped[list[date]] = mapped_column(ARRAY(Date))
    obsid: Mapped[list[int | None]] = mapped_column(ARRAY(BigInteger))
    loknr: Mapped[list[int | None]] = mapped_column(ARRAY(Integer))


class KlImport(Base):
    """En indlæsning af World Bird List (AviList): diff vises før den anvendes."""

    __tablename__ = "kl_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(300), default="")
    version: Mapped[str] = mapped_column(String(40), default="")
    status: Mapped[str] = mapped_column(String(10), default="diff")  # diff / anvendt / afvist
    rows: Mapped[list] = mapped_column(JSONB, default=list)
    diff: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
