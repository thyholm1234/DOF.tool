"""FiNN (Fugle i Ny Natur): områderne og punkterne til FiNN-tællinger, gemt i portalen, så de kan rettes i
Admin → Tællinger. DOFbasen er kilden (services/finn.py henter dens offentlige API); hver række husker DOFbasens seneste
version i `dof`, så en synkronisering kan se, hvad der er ændret hos DOFbasen, og hvad portalen selv har rettet."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class FinnSite(Base):
    """Et FiNN-område. id er DOFbasens id; områder oprettet i portalen får id'er fra 1.000.000 (origin = portal)."""

    __tablename__ = "finn_sites"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    origin: Mapped[str] = mapped_column(String(10), default="dofbasen")  # dofbasen / portal
    title: Mapped[str] = mapped_column(String(200))
    loknr: Mapped[int | None] = mapped_column(Integer)  # DOFbasens lokalitet (lokId), hvis området har én
    ring: Mapped[list] = mapped_column(JSONB, default=list)  # [[lat, lon], …] – den gældende grænse
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")  # vises i appen
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")  # portalen har rettet titel/lokalitet/grænse
    note: Mapped[str | None] = mapped_column(Text)
    dof: Mapped[dict | None] = mapped_column(JSONB)  # DOFbasens seneste version: {title, loknr, ring}
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # findes ikke længere hos DOFbasen
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class FinnPoint(Base):
    """Et FiNN-punkt: referencepunkt (tæl gerne her) eller forvaltningspunkt. source: researcher / volunteer."""

    __tablename__ = "finn_points"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    origin: Mapped[str] = mapped_column(String(10), default="dofbasen")
    site_id: Mapped[int | None] = mapped_column(Integer, index=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    source: Mapped[str | None] = mapped_column(String(12))
    type: Mapped[str | None] = mapped_column(String(12))  # reference / management
    aktiv: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    note: Mapped[str | None] = mapped_column(Text)
    dof: Mapped[dict | None] = mapped_column(JSONB)  # {site, lat, lon, source, type}
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class FinnSync(Base):
    """Én hentning fra DOFbasen: det hentede (data), ændringsrapporten og – når admin har valgt – hvad der blev anvendt."""

    __tablename__ = "finn_syncs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="klar")  # klar / anvendt / forkastet
    data: Mapped[dict] = mapped_column(JSONB, default=dict)  # {"sites": […], "points": […]} som services/finn.build
    report: Mapped[dict] = mapped_column(JSONB, default=dict)
    applied: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
