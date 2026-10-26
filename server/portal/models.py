"""Databasemodel.

Observationer er normaliseret i observers/trips/observations, så hele arkivet (2010→) kan
ligge i én database. Geometrier ligger på lokaliteter og områder og bruges kun via PostGIS-SQL.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, deferred, mapped_column

from portal.db import Base, Geometry

# ---------------------------------------------------------------- referencedata


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


# ---------------------------------------------------------------- observationer


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


# ---------------------------------------------------------------- aggregater (fænologi)


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


# ---------------------------------------------------------------- brugere


class Role(Base):
    """Roller og deres admin-moduler (redigeres af superadmin under Admin → Roller). Superadmin er ikke en række: den
    har altid alle moduler og styres fra .env."""

    __tablename__ = "roles"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    modules: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    sort: Mapped[int] = mapped_column(Integer, default=100)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    obserkode: Mapped[str] = mapped_column(String(16), unique=True)
    navn: Mapped[str | None] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user / editor / admin / superadmin
    dof_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Nye brugere skal godkendes af en administrator (kan slås fra). Eksisterende brugere er godkendt.
    approved: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    # Profil: hjemafdelinger, kommune, listedeltagelse, stille timer, tema m.m.
    prefs: Mapped[dict] = mapped_column(JSONB, default=dict)
    history_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Adgang til admin-moduler (se services.auth.MODULES). None = rollens standardmoduler.
    permissions: Mapped[list[str] | None] = mapped_column(JSONB)
    # Testbrugere oprettes af en administrator og logger ind med en lokal adgangskode (ikke hos DOFbasen)
    is_test: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    test_pw_hash: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserLocation(Base):
    """Seneste kendte position for en app-bruger (baggrundsposition). Bruges til 'følg mig'-radius i feed og alarmer."""

    __tablename__ = "user_locations"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    accuracy_m: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(12), default="app")  # app / web
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class PushSubscription(Base):
    __tablename__ = "push_subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(Text, unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    user_agent: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_ok: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Area(Base):
    """Et område en bruger har defineret: afdeling, kommune, lokalitet, polygon eller radius."""

    __tablename__ = "areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    kind: Mapped[str] = mapped_column(String(12))  # afdeling / kommune / lokalitet / polygon / radius
    ref: Mapped[str | None] = mapped_column(String(40))
    radius_m: Mapped[int | None] = mapped_column(Integer)
    geom = deferred(mapped_column(Geometry("Geometry")))
    # Indgår i årslister og scoreboards
    use_for_lists: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    area_id: Mapped[int | None] = mapped_column(ForeignKey("areas.id", ondelete="CASCADE"))
    # Alternativ til area_id: en eller flere kommuner/lokalafdelinger direkte (reglen gælder i dem alle)
    kommuner: Mapped[list[str]] = mapped_column(ARRAY(String(4)), default=list)
    afdelinger: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    # Kommasepareret valg af SU, SUB, bemaerk, local – eller "all"
    level: Mapped[str] = mapped_column(String(32), default="SU,SUB")
    local_max: Mapped[int] = mapped_column(Integer, default=5)
    # Lokale hits tælles som estimerede fund på lokaliteten, meldingen kommer fra ("lokalitet"), eller i hele reglens
    # område ("omraade")
    local_basis: Mapped[str] = mapped_column(String(10), default="lokalitet", server_default="lokalitet")
    min_antal: Mapped[int] = mapped_column(Integer, default=1)
    species_include: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    species_exclude: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    # Kun arter brugeren endnu ikke har krydset i området: "year" (årslisten) eller "ever" (alle år); None = alle arter
    missing: Mapped[str | None] = mapped_column(String(8))
    push: Mapped[bool] = mapped_column(Boolean, default=True)
    # Kun den første indtastning af arten på lokaliteten pr. dag – ikke ny besked når flere eller flere ind. indtastes
    first_only: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadFollow(Base):
    __tablename__ = "thread_follows"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    dato: Mapped[date] = mapped_column(Date, primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    loknr: Mapped[int] = mapped_column(Integer, primary_key=True)
    muted: Mapped[bool] = mapped_column(Boolean, default=False)


class ThreadComment(Base):
    __tablename__ = "thread_comments"
    __table_args__ = (Index("ix_comment_thread", "dato", "artnr", "loknr"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dato: Mapped[date] = mapped_column(Date)
    artnr: Mapped[int] = mapped_column(Integer)
    loknr: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ThreadStatus(Base):
    """Statusmeldinger på en SU/SUB-tråd (art + lokalitet + dag): stadig til stede, fløjet eller ikke genfundet.
    Alle indloggede kan melde; en melding kan trækkes tilbage af den, der meldte den."""

    __tablename__ = "thread_statuses"
    __table_args__ = (Index("ix_thread_status", "dato", "artnr", "loknr"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dato: Mapped[date] = mapped_column(Date)
    artnr: Mapped[int] = mapped_column(Integer)
    loknr: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Notification(Base):
    """Indbakke + log over sendte notifikationer (dedup pr. bruger/observation)."""

    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("user_id", "dedup_key", name="uq_notification_dedup"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dedup_key: Mapped[str] = mapped_column(String(80))
    rule_id: Mapped[int | None] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(250))
    body: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(300))
    kategori: Mapped[str | None] = mapped_column(String(8))
    pushed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    read: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class ListGroup(Base):
    __tablename__ = "list_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    owner_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    invite_code: Mapped[str] = mapped_column(String(16), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ListGroupMember(Base):
    __tablename__ = "list_group_members"

    group_id: Mapped[int] = mapped_column(ForeignKey("list_groups.id", ondelete="CASCADE"), primary_key=True)
    obserkode: Mapped[str] = mapped_column(String(16), primary_key=True)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(60), primary_key=True)
    value: Mapped[dict | list | str | int | None] = mapped_column(JSONB)


class NewsPost(Base):
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MobPreset(Base):
    """Gemt opsætning i Mobgang-generatoren: arter til/fra, antal, styrke og targetart med boost.
    Officielle presets ses af alle og vedligeholdes af redaktører; øvrige er private for brugeren."""

    __tablename__ = "mob_presets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    official: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    name: Mapped[str] = mapped_column(String(120))
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


# ---------------------------------------------------------------- læring


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text, default="")
    # id / kald / sæson / andet
    topic: Mapped[str] = mapped_column(String(16), default="id")
    level: Mapped[str] = mapped_column(String(16), default="øvet")
    intro: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text)
    media_kind: Mapped[str | None] = mapped_column(String(8))  # image / audio
    media_url: Mapped[str | None] = mapped_column(String(400))
    media_credit: Mapped[str | None] = mapped_column(String(200))
    # [{"text": "...", "correct": true}, ...]
    choices: Mapped[list] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text, default="")


class QuestionProgress(Base):
    """Spaced repetition (Leitner-kasser) pr. bruger og spørgsmål."""

    __tablename__ = "question_progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    box: Mapped[int] = mapped_column(SmallInteger, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    wrong: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuizResult(Base):
    __tablename__ = "quiz_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    quiz_key: Mapped[str] = mapped_column(String(80))
    score: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- egne indtastninger (feltmeldinger)


class FieldTrip(Base):
    """En tur i portalen, som i DOFbasens indtastning: dato, lokalitet, tider, metode, vejr, GPS-rute og observationer.

    obserkode er observatøren (kan være en anden end brugeren, når der indtastes på vegne af); user_id er den, der
    har indtastet. client_id gør upload fra offline-kø idempotent."""

    __tablename__ = "field_trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    obserkode: Mapped[str] = mapped_column(String(16))
    client_id: Mapped[str | None] = mapped_column(String(40))
    dato: Mapped[date] = mapped_column(Date, index=True)
    loknr: Mapped[int] = mapped_column(Integer, index=True)
    turtidfra: Mapped[str | None] = mapped_column(String(5))
    turtidtil: Mapped[str | None] = mapped_column(String(5))
    medobser: Mapped[str | None] = mapped_column(String(300))
    turnoter: Mapped[str | None] = mapped_column(Text)
    metode_id: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    komplet: Mapped[bool | None] = mapped_column(Boolean)  # komplet artsliste
    spontan: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    temperatur: Mapped[int | None] = mapped_column(SmallInteger)
    skydaekke: Mapped[int | None] = mapped_column(SmallInteger)  # ottendedele
    vindretning: Mapped[str | None] = mapped_column(String(4))
    vindstyrke: Mapped[int | None] = mapped_column(SmallInteger)  # m/s
    nedboer: Mapped[int | None] = mapped_column(SmallInteger)
    nedboertype: Mapped[str | None] = mapped_column(String(8))
    sigtbarhed: Mapped[int | None] = mapped_column(Integer)
    gps_aktiv: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    gps_track: Mapped[list | None] = mapped_column(JSONB)  # [[lat, lon, sekunder siden midnat/epoch, nøjagtighed], ...]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_field_trips_client", "user_id", "client_id", unique=True,
                            postgresql_where=text("client_id IS NOT NULL")),)


class FieldBreedingPair(Base):
    """Ynglepar registreret på en tur (som 'Ny indtastning af ynglepar' i DOFbasen-appen)."""

    __tablename__ = "field_breeding_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("field_trips.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str | None] = mapped_column(String(40))
    artnr: Mapped[int] = mapped_column(Integer)
    antal: Mapped[int] = mapped_column(Integer, default=1)
    adfkode: Mapped[str | None] = mapped_column(String(16))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    noter: Mapped[str | None] = mapped_column(Text)
    hemmelig: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldDisturbance(Base):
    """Forstyrrelse (jagt, hunde, sejlads …) noteret på en tur."""

    __tablename__ = "field_disturbances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("field_trips.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str | None] = mapped_column(String(40))
    type_id: Mapped[int] = mapped_column(SmallInteger)
    antal: Mapped[int | None] = mapped_column(Integer)
    noter: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldReport(Base):
    """Én observation indtastet i portalen. Felterne følger DOFbasen; status og sikkerhed er portalens egne."""

    __tablename__ = "field_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kun ældre meldinger hører til en tur; nye er én fugl med dato, koordinat og lokalitet
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("field_trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    obserkode: Mapped[str] = mapped_column(String(16))
    dato: Mapped[date] = mapped_column(Date, index=True)
    loknr: Mapped[int] = mapped_column(Integer)
    afdelinger: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    artnr: Mapped[int] = mapped_column(Integer)
    antal: Mapped[int] = mapped_column(Integer, default=1)
    koen: Mapped[str | None] = mapped_column(String(4))
    adfkode: Mapped[str | None] = mapped_column(String(16))
    alderkode: Mapped[str | None] = mapped_column(String(8))
    dragtkode: Mapped[str | None] = mapped_column(String(8))
    metode: Mapped[str | None] = mapped_column(String(80))
    obstidfra: Mapped[str | None] = mapped_column(String(5))
    obstidtil: Mapped[str | None] = mapped_column(String(5))
    obs_lat: Mapped[float | None] = mapped_column(Float)
    obs_lon: Mapped[float | None] = mapped_column(Float)
    radius_m: Mapped[int | None] = mapped_column(Integer)
    fuglnoter: Mapped[str | None] = mapped_column(Text)
    hemmelig: Mapped[bool] = mapped_column(Boolean, default=False)
    skjul_position: Mapped[bool] = mapped_column(Boolean, default=False)
    # Sikkerhed: hvor sikker er bestemmelsen, og hvad bygger den på
    sikkerhed: Mapped[str] = mapped_column(String(12), default="sikker")
    grundlag: Mapped[list[str]] = mapped_column(ARRAY(String(12)), default=list)
    retningslinjer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Under observation: til_stede / fløjet / ikke_genfundet / ukendt (seneste statusopdatering)
    status: Mapped[str] = mapped_column(String(16), default="ukendt")
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media: Mapped[list] = mapped_column(JSONB, default=list)
    kategori: Mapped[str] = mapped_column(String(8), default="alm")
    antalmetode: Mapped[int | None] = mapped_column(SmallInteger)  # 1 uangivet, 2 præcis, 3 estimat
    maerker: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list, server_default="{}")  # kalder, hoert, burfugl …
    client_id: Mapped[str | None] = mapped_column(String(40))  # fra offline-kø: matcher gentagne uploads af samme tur
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_field_reports_thread", "dato", "artnr", "loknr"),)


class FieldReportUpdate(Base):
    """Statusmeldinger på en feltmelding – fra indtasteren eller andre, der står ved fuglen."""

    __tablename__ = "field_report_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("field_reports.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- matrikler (boligbirding)


class Matrikel(Base):
    """En brugers matrikel. Nummer 1 tæller på scoreboards (#BB26 / #BB26-1), 2+ er private (#BB26-2 ...)."""

    __tablename__ = "matrikler"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nummer: Mapped[int] = mapped_column(SmallInteger)
    navn: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "nummer"),)


class MatrikelPeriode(Base):
    """Periode hvor en matrikel ligger et bestemt sted. Ved flytning afsluttes én periode og en ny starter."""

    __tablename__ = "matrikel_perioder"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    matrikel_id: Mapped[int] = mapped_column(ForeignKey("matrikler.id", ondelete="CASCADE"), index=True)
    navn: Mapped[str] = mapped_column(String(160))
    start: Mapped[date] = mapped_column(Date)
    slut: Mapped[date | None] = mapped_column(Date)


# ---------------------------------------------------------------- likes, aktivitet og support


class Like(Base):
    """Like på en tråd (fuglen: dato/artnr/loknr) eller en kommentar (id)."""

    __tablename__ = "likes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(12))
    key: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("user_id", "kind", "key"), Index("ix_likes_target", "kind", "key"))


class Activity(Base):
    """Aktivitetslog: sidevisninger og handlinger (slettes efter 180 dage)."""

    __tablename__ = "activity"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32))
    path: Mapped[str | None] = mapped_column(String(200))
    detail: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    __table_args__ = (Index("ix_activity_user", "user_id", "created_at"),)


class SupportThread(Base):
    __tablename__ = "support_threads"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(16), default="feedback")  # feedback / support / fejl / ide
    status: Mapped[str] = mapped_column(String(12), default="open")  # open / closed
    unread_admin: Mapped[bool] = mapped_column(Boolean, default=True)
    unread_user: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SupportMessage(Base):
    __tablename__ = "support_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    thread_id: Mapped[int] = mapped_column(ForeignKey("support_threads.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    from_admin: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str] = mapped_column(Text)
    context: Mapped[dict | None] = mapped_column(JSONB)  # side, appversion, browser
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CommentBan(Base):
    """Udelukkelse fra at kommentere (som blacklisten i DOF.not). Kan være tidsbegrænset; med hide_comments
    ses brugerens kommentarer kun af brugeren selv, så længe udelukkelsen gælder."""

    __tablename__ = "comment_bans"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    reason: Mapped[str] = mapped_column(Text)
    evidence: Mapped[str | None] = mapped_column(Text)  # kommentaren der førte til udelukkelsen
    hide_comments: Mapped[bool] = mapped_column(Boolean, default=True)
    until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# ---------------------------------------------------------------- datastatus: DOFbasen-måneder og vejrarkiv


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


# ---------------------------------------------------------------- DOF-ture og lokalitetskalendere


class DofEvent(Base):
    """Tur/aktivitet fra dof.dk (Ture og aktiviteter), kodet med lokalafdeling, kommune og nærmeste lokalitet."""

    __tablename__ = "dof_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)  # dof.dk event_id
    title: Mapped[str] = mapped_column(String(300))
    dato: Mapped[date] = mapped_column(Date, index=True)
    tid_fra: Mapped[str | None] = mapped_column(String(5))
    tid_til: Mapped[str | None] = mapped_column(String(5))
    types: Mapped[list[str]] = mapped_column(ARRAY(String(40)), default=list)
    arrangoer: Mapped[str | None] = mapped_column(String(300))
    afdeling_id: Mapped[int | None] = mapped_column(SmallInteger, index=True)
    kommune_kode: Mapped[str | None] = mapped_column(String(4))
    commune: Mapped[str | None] = mapped_column(String(80))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    loknr: Mapped[int | None] = mapped_column(Integer, index=True)
    lok_dist_m: Mapped[int | None] = mapped_column(Integer)
    beskrivelse: Mapped[str | None] = mapped_column(Text)
    tilmelding: Mapped[str | None] = mapped_column(Text)
    turleder: Mapped[str | None] = mapped_column(Text)
    moedested: Mapped[str | None] = mapped_column(Text)
    listed: Mapped[bool] = mapped_column(Boolean, default=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    detail_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(8), default="dof", server_default="dof")  # dof / portal (oprettet af moderator)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))


class SiteCalendar(Base):
    """Fælles kalender for en lokalitet (fx en trækpost). Én pr. lokalitet; en bruger kan oprette højst 5."""

    __tablename__ = "site_calendars"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    loknr: Mapped[int] = mapped_column(Integer, unique=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16), default="traekpost")  # traekpost / seawatch / ringmaerkning / andet
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class CalendarVisit(Base):
    """En brugers planlagte besøg på en dag (forventet tidsrum)."""

    __tablename__ = "calendar_visits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    calendar_id: Mapped[int] = mapped_column(ForeignKey("site_calendars.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    dato: Mapped[date] = mapped_column(Date, index=True)
    tid_fra: Mapped[str] = mapped_column(String(5))
    tid_til: Mapped[str | None] = mapped_column(String(5))
    visibility: Mapped[str] = mapped_column(String(8), default="public")  # public / private
    companions: Mapped[str | None] = mapped_column(String(300))
    note: Mapped[str | None] = mapped_column(String(500))
    show_phone: Mapped[bool] = mapped_column(Boolean, default=False)
    lat: Mapped[float | None] = mapped_column(Float)  # præcis position/mødested (valgfri)
    lon: Mapped[float | None] = mapped_column(Float)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (UniqueConstraint("calendar_id", "user_id", "dato"),)


# ---------------------------------------------------------------- DOFbasen (showcase af en ny dofbasen.dk)


class DofbPage(Base):
    """Tekstside i DOFbasen-modulet (Hvad er det?, Rettigheder, Hjælp …). Sektioner med let markup, redigeres af redaktører."""

    __tablename__ = "dofb_pages"

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    sections: Mapped[list] = mapped_column(JSONB, default=list)  # [{title, body}]
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbNews(Base):
    """Nyt på DOFbasen."""

    __tablename__ = "dofb_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dato: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)  # vises under listen (fx "Kendte fejl")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbFocus(Base):
    """Forsidens FOKUS-bokse (temaer); de aktive vises som faneblade."""

    __tablename__ = "dofb_focus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=100)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbList(Base):
    """Redigerbar opslagsliste (SU-arter, adfærdskoder, links …): kolonner og rækker som tekst."""

    __tablename__ = "dofb_lists"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    intro: Mapped[str] = mapped_column(Text, default="")
    columns: Mapped[list] = mapped_column(JSONB, default=list)
    rows: Mapped[list] = mapped_column(JSONB, default=list)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbArea(Base):
    """Storlokalitet eller IBA-område: en navngiven samling DOFbasen-lokaliteter (bruges i søgning og lister)."""

    __tablename__ = "dofb_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)  # stor / iba
    dof_id: Mapped[int | None] = mapped_column(Integer)  # nummeret på dofbasen.dk (storlokal=…)
    navn: Mapped[str] = mapped_column(String(200))
    loknrs: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)


class DofbRole(Base):
    """DOFbasen-modulets egne roller (adskilt fra portalens roller) og deres rettigheder."""

    __tablename__ = "dofb_roles"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    perms: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    sort: Mapped[int] = mapped_column(Integer, default=100)


class DofbUserRole(Base):
    __tablename__ = "dofb_user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_key: Mapped[str] = mapped_column(ForeignKey("dofb_roles.key", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbApiGrant(Base):
    """Adgang til DOFbasen-API'et, tildelt af en administrator. Brugeren laver selv sine tokens."""

    __tablename__ = "dofb_api_grants"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rate_per_min: Mapped[int] = mapped_column(Integer, default=60)
    max_tokens: Mapped[int] = mapped_column(Integer, default=5)
    note: Mapped[str | None] = mapped_column(String(300))


class DofbApiToken(Base):
    """Personligt API-token. Kun en SHA-256 af tokenet gemmes; selve tokenet vises én gang ved oprettelse."""

    __tablename__ = "dofb_api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses: Mapped[int] = mapped_column(BigInteger, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DofbObsStatus(Base):
    """Kvalitetssikring (DKU) af en observation: til behandling (blå pil), behandlet af DKU eller godkendt af SU."""

    __tablename__ = "dofb_obs_status"

    obsid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(String(12))  # behandling / dku / su
    note: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[int | None] = mapped_column(Integer)  # users.id (ingen fremmednøgle: tabellen ligger i observationsgruppen)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbAudit(Base):
    """Log over ændringer i DOFbasen-modulet (vises i admin-panelets oversigt)."""

    __tablename__ = "dofb_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


# ---------------------------------------------------------------- Netfugl (showcase af en ny netfugl.dk)


class NfPage(Base):
    """Tekstside i Netfugl-modulet (Om Netfugl, FAQ, Krydsregler …) i let markup."""

    __tablename__ = "nf_pages"

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfArticle(Base):
    """Nyhed/artikel (kind=artikel) eller kort nyt fra ind- og udland (kind=kort)."""

    __tablename__ = "nf_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="artikel")
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text, default="")
    writers: Mapped[str | None] = mapped_column(String(300))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    dato: Mapped[date] = mapped_column(Date, index=True)
    image_url: Mapped[str | None] = mapped_column(String(400))
    source_url: Mapped[str | None] = mapped_column(String(400))
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfComment(Base):
    """Kommentar til en artikel eller et foto."""

    __tablename__ = "nf_comments"
    __table_args__ = (Index("ix_nf_comments_target", "target_kind", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(8))  # artikel / foto
    target_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class NfPhoto(Base):
    """Foto i galleriet. Kan knyttes til en observation i DOFbasen (obsid). Nye fotos kan kræve godkendelse."""

    __tablename__ = "nf_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(400))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    artnr: Mapped[int | None] = mapped_column(Integer, index=True)
    art_navn: Mapped[str] = mapped_column(String(200))
    latin: Mapped[str | None] = mapped_column(String(200))
    dato: Mapped[date | None] = mapped_column(Date)
    lokalitet: Mapped[str | None] = mapped_column(String(250))
    loknr: Mapped[int | None] = mapped_column(Integer)
    land: Mapped[str] = mapped_column(String(80), default="Danmark")
    alder: Mapped[str | None] = mapped_column(String(40))
    beskrivelse: Mapped[str | None] = mapped_column(Text)
    obsid: Mapped[int | None] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(12), default="publiceret")  # afventer / publiceret / afvist
    views: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class NfLike(Base):
    __tablename__ = "nf_likes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfSpecies(Base):
    """Arter på DK-listen og VP-listen. artnr kobler til DOFbasens art (via latinsk navn)."""

    __tablename__ = "nf_species"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    liste: Mapped[str] = mapped_column(String(4), index=True)  # dk / vp
    sort: Mapped[int] = mapped_column(Integer, default=0)
    su: Mapped[bool] = mapped_column(Boolean, default=False)
    navn: Mapped[str] = mapped_column(String(200))
    latin: Mapped[str] = mapped_column(String(200))
    engelsk: Mapped[str | None] = mapped_column(String(200))
    kategori: Mapped[str | None] = mapped_column(String(4))
    artnr: Mapped[int | None] = mapped_column(Integer)


class NfFamily(Base):
    __tablename__ = "nf_families"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    orden: Mapped[str] = mapped_column(String(60))
    familie: Mapped[str] = mapped_column(String(80))
    engelsk: Mapped[str] = mapped_column(String(160))
    antal: Mapped[int] = mapped_column(Integer, default=0)


class NfLink(Base):
    __tablename__ = "nf_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gruppe: Mapped[str] = mapped_column(String(60))
    titel: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(String(400))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class NfFeed(Base):
    """RSS-kilde til Netfugl live. feed_url findes automatisk fra site_url (<link rel=alternate>), hvis den mangler."""

    __tablename__ = "nf_feeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(160))
    site_url: Mapped[str | None] = mapped_column(String(400))
    feed_url: Mapped[str | None] = mapped_column(String(400))
    land: Mapped[str] = mapped_column(String(60), default="Danmark")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetch: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(400))
    items: Mapped[int] = mapped_column(Integer, default=0)


class NfFeedItem(Base):
    __tablename__ = "nf_feed_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("nf_feeds.id", ondelete="CASCADE"), index=True)
    guid: Mapped[str] = mapped_column(String(400))
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str | None] = mapped_column(String(600))
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (UniqueConstraint("feed_id", "guid"),)


class NfKryds(Base):
    """Manuelt kryds på en krydsliste (supplerer de automatiske fra DOFbasen). hidden=True skjuler et automatisk kryds."""

    __tablename__ = "nf_kryds"
    __table_args__ = (Index("ix_nf_kryds_list", "liste", "obserkode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    obserkode: Mapped[str] = mapped_column(String(16), index=True)
    liste: Mapped[str] = mapped_column(String(24))  # dk / vp / falskvp / ring / aedel
    artnr: Mapped[int | None] = mapped_column(Integer)
    latin: Mapped[str] = mapped_column(String(200))
    navn: Mapped[str] = mapped_column(String(200))
    dato: Mapped[date | None] = mapped_column(Date)
    lokalitet: Mapped[str | None] = mapped_column(String(250))
    land: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(String(500))
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfAutoList(Base):
    """Automatiske kryds beregnet af workeren ud fra DOFbasen-observationer: første fund pr. art på én deltagers liste.
    Én række pr. deltager og liste med parallelle arrays (element i = én art) – ikke én række pr. kryds, som kostede en
    rækkeheader og en indekspost pr. kryds; store arrays komprimeres desuden af Postgres."""

    __tablename__ = "nf_auto_lists"

    obserkode: Mapped[str] = mapped_column(String(16), primary_key=True)
    liste: Mapped[str] = mapped_column(String(24), primary_key=True)  # dk / aar:2026 / md:1 / afd:574 / kom:0101
    artnr: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    dato: Mapped[list[date]] = mapped_column(ARRAY(Date))
    obsid: Mapped[list[int | None]] = mapped_column(ARRAY(BigInteger))
    loknr: Mapped[list[int | None]] = mapped_column(ARRAY(Integer))


class NfRole(Base):
    """Netfugl-modulets egne roller og rettigheder (adskilt fra portalens og DOFbasens roller)."""

    __tablename__ = "nf_roles"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    perms: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    sort: Mapped[int] = mapped_column(Integer, default=100)


class NfUserRole(Base):
    __tablename__ = "nf_user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_key: Mapped[str] = mapped_column(ForeignKey("nf_roles.key", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfApiGrant(Base):
    __tablename__ = "nf_api_grants"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rate_per_min: Mapped[int] = mapped_column(Integer, default=60)
    max_tokens: Mapped[int] = mapped_column(Integer, default=5)
    note: Mapped[str | None] = mapped_column(String(300))


class NfApiToken(Base):
    __tablename__ = "nf_api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses: Mapped[int] = mapped_column(BigInteger, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NfAudit(Base):
    __tablename__ = "nf_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)



# ---------------------------------------------------------------- DKU: tip om tvivlsomme observationer

class DkuMember(Base):
    """DKU-ansvarlig for en lokalafdeling. afdeling_id 0 = landsdækkende (ser sager fra alle afdelinger)."""

    __tablename__ = "dku_members"

    afdeling_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True, autoincrement=False)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    added_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DkuReview(Base):
    """DKU-ansvarliges gennemgang af en kandidat (lokal afvigelse): godkendt = ingen sag nødvendig. Godkendte observationer
    vises ikke igen i kandidatlisten. (En sag oprettes som almindelig DkuCase.)"""

    __tablename__ = "dku_reviews"

    obsid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    decision: Mapped[str] = mapped_column(String(10), default="godkendt", server_default="godkendt")
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    afdelinger: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DkuCandidate(Base):
    """Kandidat fundet af det løbende filter (services/dku_candidates.py). data er rækken som fra dku.candidates();
    sag og godkendelse slås op, når listen vises. found_at er første gang observationen ramte filtret."""

    __tablename__ = "dku_candidates"

    obsid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    dato: Mapped[date] = mapped_column(Date, index=True)
    afdelinger: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    matched: Mapped[list[str]] = mapped_column(ARRAY(String(10)), default=list)
    data: Mapped[dict] = mapped_column(JSONB)
    found_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DkuCase(Base):
    """En sag om én observation: DOFbasen (obsid) eller en feltmelding i portalen (report_id).

    snapshot er observationens data, da sagen blev oprettet – så DKU kan se, hvad der er rettet siden. Observatøren ser
    først sagen, når DKU har inddraget vedkommende (observer_involved), og får aldrig at vide, om sagen startede med et tip
    eller hos DKU selv (origin vises kun for DKU)."""

    __tablename__ = "dku_cases"
    __table_args__ = (Index("ix_dku_cases_obs", "source", "obsid", "report_id"), Index("ix_dku_cases_status", "status", "updated_at"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(10))  # dofbasen / portal
    origin: Mapped[str] = mapped_column(String(6), default="tip", server_default="tip")  # tip / dku (kun synligt for DKU)
    obsid: Mapped[int | None] = mapped_column(BigInteger)
    report_id: Mapped[int | None] = mapped_column(ForeignKey("field_reports.id", ondelete="SET NULL"))
    obserkode: Mapped[str | None] = mapped_column(String(16))
    observer_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    artnr: Mapped[int] = mapped_column(Integer)
    dato: Mapped[date] = mapped_column(Date)
    loknr: Mapped[int | None] = mapped_column(Integer)
    afdelinger: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    snapshot: Mapped[dict] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(String(16))  # dokumentation / usandsynlig / fejl / andet
    status: Mapped[str] = mapped_column(String(12), default="ny")  # ny / behandles / afventer / afgjort
    outcome: Mapped[str | None] = mapped_column(String(12))  # godkendt / rettet / afvist / henlagt
    outcome_note: Mapped[str | None] = mapped_column(Text)
    observer_involved: Mapped[bool] = mapped_column(Boolean, default=False)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DkuParticipant(Base):
    """Hvem der er med i en sag (tipper, observer eller dku) og hvornår de sidst har læst den."""

    __tablename__ = "dku_participants"

    case_id: Mapped[int] = mapped_column(ForeignKey("dku_cases.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(10))
    last_read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DkuMessage(Base):
    """Besked i en sag. visibility er kanalen: dku (intern note), tipper (DKU ↔ tippere) eller observer (DKU ↔ observatøren).
    En tipper ser kun sine egne beskeder og DKU's svar til tippere – tippere og observatøren kan aldrig skrive til hinanden."""

    __tablename__ = "dku_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("dku_cases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(String(10))  # tipper / observer / dku / system
    visibility: Mapped[str] = mapped_column(String(10), default="dku")
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


# Ekstra DDL der ikke kan udtrykkes pænt i modellerne
EXTRA_DDL = [
    # Netfugls automatiske kryds ligger nu i nf_auto_lists (én række pr. deltager og liste); den gamle tabel var afledt data
    "DROP TABLE IF EXISTS nf_auto",
    # Portalen gemmer ikke længere noget om adgangskoder – login tjekkes altid hos DOFbasen
    "ALTER TABLE users DROP COLUMN IF EXISTS password_hash",
    "ALTER TABLE notifications ALTER COLUMN pushed SET DEFAULT false",
    "ALTER TABLE notifications ALTER COLUMN read SET DEFAULT false",
    "CREATE INDEX IF NOT EXISTS ix_locations_geom ON locations USING gist (geom)",
    "CREATE INDEX IF NOT EXISTS ix_kommuner_geom ON kommuner USING gist (geom)",
    "CREATE INDEX IF NOT EXISTS ix_areas_geom ON areas USING gist (geom)",
    "CREATE INDEX IF NOT EXISTS ix_obs_afdelinger ON observations USING gin (afdelinger)",
    "CREATE INDEX IF NOT EXISTS ix_obs_notable ON observations (dato) WHERE kategori <> 'alm'",
    "CREATE INDEX IF NOT EXISTS ix_trips_bbtag ON trips (obserkode, dato) WHERE turnoter LIKE '%#%'",
    # 10 km-kvadrat (DKN, EPSG:25832) for hver lokalitet – nøgle til vejrarkivet
    "ALTER TABLE locations ADD COLUMN IF NOT EXISTS cell10 integer",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS first_only boolean NOT NULL DEFAULT false",
    # Lokale hits efter estimerede fund: eksisterende regler talte i hele området og gør det fortsat
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS local_basis varchar(10) NOT NULL DEFAULT 'omraade'",
    "ALTER TABLE alert_rules ALTER COLUMN local_basis SET DEFAULT 'lokalitet'",
    "ALTER TABLE area_species_counts ADD COLUMN IF NOT EXISTS n_finds integer NOT NULL DEFAULT 0",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS retningslinjer boolean NOT NULL DEFAULT false",
    "ALTER TABLE field_reports ALTER COLUMN trip_id DROP NOT NULL",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS antalmetode smallint",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS maerker varchar(16)[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE field_reports ADD COLUMN IF NOT EXISTS client_id varchar(40)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS client_id varchar(40)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS metode_id smallint NOT NULL DEFAULT 1",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS komplet boolean",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS spontan boolean NOT NULL DEFAULT false",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS lat double precision",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS lon double precision",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS temperatur smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS skydaekke smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS vindretning varchar(4)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS vindstyrke smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS nedboer smallint",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS nedboertype varchar(8)",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS sigtbarhed integer",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS gps_aktiv boolean NOT NULL DEFAULT false",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS gps_track jsonb",
    "ALTER TABLE field_trips ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now()",
    "CREATE UNIQUE INDEX IF NOT EXISTS ix_field_trips_client ON field_trips (user_id, client_id) WHERE client_id IS NOT NULL",
    "ALTER TABLE dof_events ADD COLUMN IF NOT EXISTS source varchar(8) NOT NULL DEFAULT 'dof'",
    "ALTER TABLE dof_events ADD COLUMN IF NOT EXISTS created_by integer REFERENCES users(id) ON DELETE SET NULL",
    "ALTER TABLE calendar_visits ADD COLUMN IF NOT EXISTS lat double precision",
    "ALTER TABLE calendar_visits ADD COLUMN IF NOT EXISTS lon double precision",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS kommuner varchar(4)[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS afdelinger smallint[] NOT NULL DEFAULT '{}'",
    "ALTER TABLE alert_rules ADD COLUMN IF NOT EXISTS missing varchar(8)",
    # Alarmniveau som afkrydsninger: de gamle trinvise niveauer omskrives til deres kategorier
    "ALTER TABLE alert_rules ALTER COLUMN level TYPE varchar(32)",
    "UPDATE alert_rules SET level = CASE level WHEN 'SUB' THEN 'SU,SUB' WHEN 'bemaerk' THEN 'SU,SUB,bemaerk'"
    " WHEN 'local' THEN 'SU,SUB,local' ELSE level END WHERE level IN ('SUB', 'bemaerk', 'local')",
    # Portalens egne ture får id'er langt fra dof.dk's event_id'er
    "CREATE SEQUENCE IF NOT EXISTS portal_event_seq START 900000000",
    # Listekategori for en observation: serie-overstyring → artens kategori på datoen (underarten før listearten)
    # → DOFbasens [klammer] om artsnavnet (ikke krydsbar) → A
    """CREATE OR REPLACE FUNCTION bird_category(a integer, la integer, l integer, d date) RETURNS varchar
    LANGUAGE sql STABLE AS $$
      SELECT coalesce(
        (SELECT kategori FROM category_overrides WHERE artnr = la AND loknr = l AND d BETWEEN date_from AND date_to
           AND removed_at IS NULL ORDER BY created_at DESC, id DESC LIMIT 1),
        (SELECT kategori FROM species_categories WHERE artnr IN (a, la) AND removed_at IS NULL
           AND coalesce(valid_from, '-infinity'::date) <= d ORDER BY (artnr = a) DESC, valid_from DESC NULLS LAST, id DESC LIMIT 1),
        (SELECT 'D' FROM species WHERE artnr IN (a, la) AND navn LIKE '[%' LIMIT 1),
        'A')
    $$""",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS permissions jsonb",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS approved boolean NOT NULL DEFAULT true",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_test boolean NOT NULL DEFAULT false",
    "ALTER TABLE users ADD COLUMN IF NOT EXISTS test_pw_hash varchar(200)",
    "ALTER TABLE mob_presets ADD COLUMN IF NOT EXISTS official boolean NOT NULL DEFAULT false",
    "ALTER TABLE mob_presets ALTER COLUMN user_id DROP NOT NULL",
    "UPDATE locations SET cell10 = (floor(ST_Y(ST_Transform(geom, 25832)) / 10000)::int * 1000"
    " + floor(ST_X(ST_Transform(geom, 25832)) / 10000)::int) WHERE cell10 IS NULL AND geom IS NOT NULL",
    # Nyt admin-modul: administratorrollen får det én gang (markør i settings), så superadmin bagefter kan fjerne det igen
    "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM settings WHERE key = 'module_added_dku') THEN"
    " UPDATE roles SET modules = array_append(modules, 'dku') WHERE key = 'admin' AND NOT ('dku' = ANY(modules));"
    " INSERT INTO settings (key, value) VALUES ('module_added_dku', 'true'); END IF; END $$",
    # DKU: adskilte kanaler i stedet for «alle»; sagens oprindelse (tip eller DKU selv)
    "ALTER TABLE dku_messages ALTER COLUMN visibility TYPE varchar(10)",
    "ALTER TABLE dku_cases ADD COLUMN IF NOT EXISTS origin varchar(6) NOT NULL DEFAULT 'tip'",
    "UPDATE dku_messages SET visibility = 'tipper' WHERE visibility = 'alle' AND role = 'tipper'",
    "UPDATE dku_messages m SET visibility = CASE WHEN c.observer_involved THEN 'observer' ELSE 'tipper' END"
    " FROM dku_cases c WHERE m.case_id = c.id AND m.visibility = 'alle'",
]
