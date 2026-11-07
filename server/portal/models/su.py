"""SU (Sjældenhedsudvalget): indsendte SU-beskrivelser (sager), deres dokumentation og beskeder samt SU's årsrapporter."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, SmallInteger, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class SuCase(Base):
    """En SU-beskrivelse. Uploaderens kontaktoplysninger ses kun af uploaderen og SU – også når sagen er godkendt og ligger
    i det åbne arkiv. status: kladde (kun uploaderen) → indsendt → behandles / afventer (SU har spurgt uploaderen) → afgjort."""

    __tablename__ = "su_cases"
    __table_args__ = (Index("ix_su_cases_status", "status", "updated_at"), Index("ix_su_cases_outcome", "outcome", "dato_fra"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sagsnr: Mapped[str | None] = mapped_column(String(16), unique=True)  # «2026-007», gives ved indsendelse
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    # 1. uploader
    up_navn: Mapped[str] = mapped_column(String(120), default="")
    up_adresse: Mapped[str] = mapped_column(String(200), default="")
    up_postnr: Mapped[str] = mapped_column(String(10), default="")
    up_by: Mapped[str] = mapped_column(String(80), default="")
    up_email: Mapped[str] = mapped_column(String(200), default="")
    up_telefon: Mapped[str] = mapped_column(String(30), default="")
    # 2. beskrivelsesinfo
    artnr: Mapped[int | None] = mapped_column(Integer)
    art: Mapped[str] = mapped_column(String(120), default="")
    dato_fra: Mapped[date | None] = mapped_column(Date)
    dato_til: Mapped[date | None] = mapped_column(Date)
    lokalitet: Mapped[str] = mapped_column(String(200), default="")
    loknr: Mapped[int | None] = mapped_column(Integer)
    region: Mapped[str] = mapped_column(String(8), default="")
    land: Mapped[str] = mapped_column(String(40), default="Danmark")
    antal: Mapped[int | None] = mapped_column(Integer)
    dragt: Mapped[str] = mapped_column(String(200), default="")
    adfaerd: Mapped[str] = mapped_column(String(200), default="")
    observatorer: Mapped[str] = mapped_column(String(500), default="")  # finder(e) med * foran navnet
    bemaerkninger: Mapped[str] = mapped_column(String(500), default="")
    beskrivelse: Mapped[str] = mapped_column(Text, default="")
    obsid: Mapped[int | None] = mapped_column(BigInteger)  # DOFbasen-observationen, hvis sagen er startet derfra
    # sagsgang
    status: Mapped[str] = mapped_column(String(10), default="kladde")
    outcome: Mapped[str | None] = mapped_column(String(14))  # godkendt / ikke_godkendt / henlagt
    outcome_note: Mapped[str | None] = mapped_column(Text)
    assignee_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    read_by_owner_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SuFile(Base):
    """Dokumentation til en sag: tekstdokumenter (kind «dokument») og foto/video/lyd/bånd (F-V-L) med kategori og info."""

    __tablename__ = "su_files"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("su_cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(10))  # dokument / foto / video / lyd / andet
    name: Mapped[str] = mapped_column(String(200))
    key: Mapped[str] = mapped_column(String(120))  # nøgle i objektlageret under su/
    content_type: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(Integer)
    info: Mapped[str] = mapped_column(Text, default="")
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SuMessage(Base):
    """Besked i en sag. internal: SU's interne note, som uploaderen aldrig ser. role: uploader / su / system."""

    __tablename__ = "su_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[int] = mapped_column(ForeignKey("su_cases.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    role: Mapped[str] = mapped_column(String(10))
    internal: Mapped[bool] = mapped_column(Boolean, default=False)
    body: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SuMemberRead(Base):
    """Hvornår et SU-medlem sidst har læst en sag (til ulæst-markering i SU's sagsliste)."""

    __tablename__ = "su_reads"

    case_id: Mapped[int] = mapped_column(ForeignKey("su_cases.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    read_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SuReport(Base):
    """SU's årsrapport (PDF). Uploades som kladde af SU og ses af alle, når den er publiceret."""

    __tablename__ = "su_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    label: Mapped[str] = mapped_column(String(20))  # «2024» eller «1995-1996»
    sort_year: Mapped[int] = mapped_column(SmallInteger, index=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    key: Mapped[str] = mapped_column(String(120))
    name: Mapped[str] = mapped_column(String(200))
    size: Mapped[int] = mapped_column(Integer)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SuObsApproval(Base):
    """SU's afgørelse på et fund af en SU-art: art × lokalitet × dag (samme nøgle som portalens tråde), så SU ikke skal
    forholde sig til hver enkelt indtastning af den samme fugl. Kun «godkendt» giver det gyldne SU-mærke på alle
    observationerne i fundet; fund uden række her afventer (intet mærke)."""

    __tablename__ = "su_obs_approvals"

    dato: Mapped[date] = mapped_column(Date, primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    loknr: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    decision: Mapped[str] = mapped_column(String(14))  # godkendt / ikke_godkendt
    note: Mapped[str] = mapped_column(Text, default="")
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
