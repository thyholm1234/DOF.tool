"""DKU: tip om tvivlsomme observationer, sager, deltagere og beskeder."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


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
