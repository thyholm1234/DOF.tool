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


class ClimateMonth(Base):
    __tablename__ = "climate_month"

    year: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    month: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    mean_temp: Mapped[float | None] = mapped_column(Float)


class AreaSpeciesCount(Base):
    """Antal historiske observationer pr. område og art – grundlag for 'lokale hits'."""

    __tablename__ = "area_species_counts"

    area_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    n_obs: Mapped[int] = mapped_column(Integer)
    n_days: Mapped[int] = mapped_column(Integer)
    last_date: Mapped[date | None] = mapped_column(Date)


# ---------------------------------------------------------------- brugere


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    obserkode: Mapped[str] = mapped_column(String(16), unique=True)
    navn: Mapped[str | None] = mapped_column(String(160))
    role: Mapped[str] = mapped_column(String(16), default="user")  # user / editor / admin / superadmin
    password_hash: Mapped[str | None] = mapped_column(String(200))
    dof_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked: Mapped[bool] = mapped_column(Boolean, default=False)
    # Profil: hjemafdelinger, kommune, listedeltagelse, stille timer, tema m.m.
    prefs: Mapped[dict] = mapped_column(JSONB, default=dict)
    history_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


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
    # SU / SUB / bemaerk / local / all
    level: Mapped[str] = mapped_column(String(8), default="SUB")
    local_max: Mapped[int] = mapped_column(Integer, default=5)
    min_antal: Mapped[int] = mapped_column(Integer, default=1)
    species_include: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    species_exclude: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)
    push: Mapped[bool] = mapped_column(Boolean, default=True)
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
    """En tur i portalen: lokalitet + dato, som i DOFbasens indtastning."""

    __tablename__ = "field_trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    obserkode: Mapped[str] = mapped_column(String(16))
    dato: Mapped[date] = mapped_column(Date, index=True)
    loknr: Mapped[int] = mapped_column(Integer, index=True)
    turtidfra: Mapped[str | None] = mapped_column(String(5))
    turtidtil: Mapped[str | None] = mapped_column(String(5))
    medobser: Mapped[str | None] = mapped_column(String(300))
    turnoter: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldReport(Base):
    """Én observation indtastet i portalen. Felterne følger DOFbasen; status og sikkerhed er portalens egne."""

    __tablename__ = "field_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("field_trips.id", ondelete="CASCADE"), index=True)
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
    # Under observation: til_stede / fløjet / ikke_genfundet / ukendt (seneste statusopdatering)
    status: Mapped[str] = mapped_column(String(16), default="ukendt")
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media: Mapped[list] = mapped_column(JSONB, default=list)
    kategori: Mapped[str] = mapped_column(String(8), default="alm")
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


# Ekstra DDL der ikke kan udtrykkes pænt i modellerne
EXTRA_DDL = [
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
    "UPDATE locations SET cell10 = (floor(ST_Y(ST_Transform(geom, 25832)) / 10000)::int * 1000"
    " + floor(ST_X(ST_Transform(geom, 25832)) / 10000)::int) WHERE cell10 IS NULL AND geom IS NOT NULL",
]
