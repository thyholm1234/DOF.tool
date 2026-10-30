"""Likes, aktivitet, support og udelukkelse fra kommentarer."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


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
