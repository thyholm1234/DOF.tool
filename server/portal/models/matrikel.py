"""Matrikler (boligbirding) og deres perioder."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


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
