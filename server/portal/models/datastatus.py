"""Datastatus: DOFbasen-måneder og vejrarkivet."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class MonthSync(Base):
    """Seneste hentning af en hel måned fra DOFbasen (månedsjob eller arkivfil)."""

    __tablename__ = "month_sync"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    rows: Mapped[int] = mapped_column(Integer, default=0)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WeatherMonth(Base):
    """DMI 10 km-grid, månedsfil: hentet/parset-status."""

    __tablename__ = "weather_months"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="missing")  # done / error / empty
    rows: Mapped[int] = mapped_column(Integer, default=0)
    cells: Mapped[int] = mapped_column(Integer, default=0)
    bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    seconds: Mapped[float | None] = mapped_column(Float)
    etag: Mapped[str | None] = mapped_column(String(120))
    last_modified: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class WeatherCellDay(Base):
    """Dagsvejr pr. 10 km-kvadrat (Det Danske Kvadratnet, kode N*1000+E). m_* = morgen kl. 4–10 lokal tid."""

    __tablename__ = "weather_cell_day"

    cell: Mapped[int] = mapped_column(Integer, primary_key=True)
    dato: Mapped[date] = mapped_column(Date, primary_key=True, index=True)
    temp: Mapped[float | None] = mapped_column(Float)
    temp_min: Mapped[float | None] = mapped_column(Float)
    temp_max: Mapped[float | None] = mapped_column(Float)
    wind: Mapped[float | None] = mapped_column(Float)
    gust: Mapped[float | None] = mapped_column(Float)
    precip: Mapped[float | None] = mapped_column(Float)
    cloud: Mapped[float | None] = mapped_column(Float)
    sun: Mapped[float | None] = mapped_column(Float)
    m_temp: Mapped[float | None] = mapped_column(Float)
    m_wind: Mapped[float | None] = mapped_column(Float)
    m_dir: Mapped[float | None] = mapped_column(Float)
    m_precip: Mapped[float | None] = mapped_column(Float)
