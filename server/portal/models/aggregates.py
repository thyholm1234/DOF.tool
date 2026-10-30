"""Afledte tal (genberegnes): fænologi, klima, artstal og fund pr. område/lokalitet, arter pr. år."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Float,
    Index,
    Integer,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class SpeciesDay(Base):
    __tablename__ = "species_day"

    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    dato: Mapped[date] = mapped_column(Date, primary_key=True)
    n_obs: Mapped[int] = mapped_column(Integer)
    n_ind: Mapped[int] = mapped_column(Integer)
    n_trips: Mapped[int] = mapped_column(Integer)
    n_migr: Mapped[int] = mapped_column(Integer)


class EffortDay(Base):
    __tablename__ = "effort_day"

    dato: Mapped[date] = mapped_column(Date, primary_key=True)
    n_trips: Mapped[int] = mapped_column(Integer)
    n_observers: Mapped[int] = mapped_column(Integer)


class PhenoMetric(Base):
    """Fænologiske nøgledatoer pr. art, år og sæson (dag-i-året)."""

    __tablename__ = "pheno_metrics"

    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    season: Mapped[str] = mapped_column(String(8), primary_key=True)  # spring / autumn
    q10: Mapped[float | None] = mapped_column(Float)
    q50: Mapped[float | None] = mapped_column(Float)
    q90: Mapped[float | None] = mapped_column(Float)
    first_doy: Mapped[int | None] = mapped_column(SmallInteger)
    mass: Mapped[float] = mapped_column(Float, default=0)
    n_days: Mapped[int] = mapped_column(SmallInteger, default=0)


class SpeciesSeason(Base):
    """Artens normale forekomstperiode (dag-i-året, 1–365, evt. hen over årsskiftet) – grundlag for filteret Fænologi."""

    __tablename__ = "species_season"

    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)  # listeart
    start_doy: Mapped[int] = mapped_column(SmallInteger)
    end_doy: Mapped[int] = mapped_column(SmallInteger)  # < start_doy: perioden går over nytår
    source: Mapped[str] = mapped_column(String(4))  # dof (bemærk-vindue) / stat (beregnet af observationerne)
    n_days: Mapped[int] = mapped_column(Integer, default=0)
    n_years: Mapped[int] = mapped_column(SmallInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ClimateMonth(Base):
    __tablename__ = "climate_month"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    mean_temp: Mapped[float | None] = mapped_column(Float)


class AreaSpeciesCount(Base):
    """Antal historiske observationer pr. område og art. n_finds = summen af estimerede fund på områdets lokaliteter
    (LocSpeciesFinds) – grundlag for 'lokale hits' i alarmregler, der tæller i hele området."""

    __tablename__ = "area_species_counts"

    area_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    n_obs: Mapped[int] = mapped_column(Integer)
    n_days: Mapped[int] = mapped_column(Integer)
    last_date: Mapped[date | None] = mapped_column(Date)
    n_finds: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class LocSpeciesFinds(Base):
    """Estimerede fund pr. lokalitet og art gennem alle år (services/locfinds.py) – grundlag for 'lokale hits'.
    Genberegnes månedligt, én lokalafdeling ad gangen."""

    __tablename__ = "loc_species_finds"

    loknr: Mapped[int] = mapped_column(Integer, primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    n_finds: Mapped[int] = mapped_column(Integer)
    n_days: Mapped[int] = mapped_column(Integer)
    last_date: Mapped[date | None] = mapped_column(Date)


class RegionYearSpecies(Base):
    """Listearter pr. region (dk / afd:N / kom:KODE) og år – grundlag for 'Arter pr. år'."""

    __tablename__ = "region_year_species"
    __table_args__ = (Index("ix_rys_region_art", "region", "artnr", "year"),)

    region: Mapped[str] = mapped_column(String(12), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    n_obs: Mapped[int] = mapped_column(Integer)
    first_date: Mapped[date] = mapped_column(Date)
    last_date: Mapped[date] = mapped_column(Date)


class RegionReach(Base):
    """Hvor mange observatører og ture der har listearten pr. region og år (year = 0: alle år) – grundlag for 'Huller'."""

    __tablename__ = "region_reach"

    region: Mapped[str] = mapped_column(String(12), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    n_observers: Mapped[int] = mapped_column(Integer)
    n_trips: Mapped[int] = mapped_column(Integer)


class RegionTotals(Base):
    """Nævnerne til RegionReach: ture og observatører i alt pr. region og år (year = 0: alle år)."""

    __tablename__ = "region_totals"

    region: Mapped[str] = mapped_column(String(12), primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    n_trips: Mapped[int] = mapped_column(Integer)
    n_observers: Mapped[int] = mapped_column(Integer)


class LocationYear(Base):
    """Tal pr. lokalitet og år (year = 0: alle år) – grundlag for Lokaliteter-siden. rank = højeste kategori i perioden
    (0 alm, 1 bemærk, 2 SUB, 3 SU)."""

    __tablename__ = "location_year"

    loknr: Mapped[int] = mapped_column(Integer, primary_key=True)
    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    n_obs: Mapped[int] = mapped_column(Integer)
    n_taxa: Mapped[int] = mapped_column(Integer)
    last_date: Mapped[date | None] = mapped_column(Date)
    rank: Mapped[int] = mapped_column(SmallInteger, default=0)
    # De seneste 30 dage på byggetidspunktet (kun brugt for indeværende år)
    n_30: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    rank30: Mapped[int] = mapped_column(SmallInteger, default=0, server_default="0")
