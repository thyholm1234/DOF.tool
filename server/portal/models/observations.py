"""Observationer fra DOFbasen: observatører, ture, observationer og synkroniseringsstatus."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class Observer(Base):
    __tablename__ = "observers"

    obserkode: Mapped[str] = mapped_column(String(16), primary_key=True)
    fornavn: Mapped[str | None] = mapped_column(String(100))
    efternavn: Mapped[str | None] = mapped_column(String(100))
    by: Mapped[str | None] = mapped_column(String(100))


class Trip(Base):
    __tablename__ = "trips"

    turid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    dato: Mapped[date] = mapped_column(Date, index=True)
    loknr: Mapped[int | None] = mapped_column(Integer, index=True)
    obserkode: Mapped[str | None] = mapped_column(String(16), index=True)
    turtidfra: Mapped[str | None] = mapped_column(String(5))
    turtidtil: Mapped[str | None] = mapped_column(String(5))
    turnoter: Mapped[str | None] = mapped_column(Text)
    medobser: Mapped[str | None] = mapped_column(Text)


class Observation(Base):
    __tablename__ = "observations"
    __table_args__ = (
        Index("ix_obs_obserkode_dato", "obserkode", "dato"),
        Index("ix_obs_artnr_dato", "artnr", "dato"),
        Index("ix_obs_loknr_artnr", "loknr", "artnr"),
        Index("ix_obs_dato_kat", "dato", "kategori"),
    )

    obsid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    turid: Mapped[int | None] = mapped_column(BigInteger, index=True)
    dato: Mapped[date] = mapped_column(Date)
    artnr: Mapped[int] = mapped_column(Integer)
    antal: Mapped[int] = mapped_column(Integer, default=0)
    koen: Mapped[str | None] = mapped_column(String(4))
    adfkode: Mapped[str | None] = mapped_column(String(8))
    alderkode: Mapped[str | None] = mapped_column(String(8))
    dragtkode: Mapped[str | None] = mapped_column(String(8))
    obstidfra: Mapped[str | None] = mapped_column(String(5))
    obstidtil: Mapped[str | None] = mapped_column(String(5))
    fuglnoter: Mapped[str | None] = mapped_column(Text)
    metode: Mapped[str | None] = mapped_column(String(60))
    hemmelig: Mapped[int] = mapped_column(SmallInteger, default=0)
    kvalitet: Mapped[int | None] = mapped_column(SmallInteger)
    loknr: Mapped[int | None] = mapped_column(Integer)
    obserkode: Mapped[str | None] = mapped_column(String(16))
    afdelinger: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    obs_lat: Mapped[float | None] = mapped_column(Float)
    obs_lon: Mapped[float | None] = mapped_column(Float)
    kategori: Mapped[str] = mapped_column(String(8), default="alm")
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DaySync(Base):
    """Hvilke datoer er hentet komplet fra DOFbasen (dagsudtræk)."""

    __tablename__ = "day_sync"

    dato: Mapped[date] = mapped_column(Date, primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rows: Mapped[int] = mapped_column(Integer, default=0)
    source: Mapped[str] = mapped_column(String(16), default="live")


class ArchiveFile(Base):
    __tablename__ = "archive_files"

    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    size: Mapped[int] = mapped_column(BigInteger)
    mtime: Mapped[float] = mapped_column(Float)
    rows: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
