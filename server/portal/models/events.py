"""DOF-ture og lokalitetskalendere."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


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
