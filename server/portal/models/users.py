"""Brugere, roller, områder, alarmer, tråde, notifikationer, listegrupper, indstillinger og nyheder."""

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
