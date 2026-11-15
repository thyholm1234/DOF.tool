"""Snak: opslag i kategorier, kommentarer med svar, reaktioner, følg og visninger (docs/snak-modul-spec.md §3).

Alt er brugerdata (se dbgroups.USER_TABLES). Ingen SQL må blande ``sn_*`` med observationstabeller."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base

REACTION_TYPES = ("like", "love", "haha", "wow", "sad", "angry")


class SnCategory(Base):
    __tablename__ = "sn_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(40), unique=True)
    navn: Mapped[str] = mapped_column(String(80))
    beskrivelse: Mapped[str] = mapped_column(String(300), default="", server_default="")
    farve: Mapped[str] = mapped_column(String(7), default="#3b6ea5", server_default="#3b6ea5")
    sort: Mapped[int] = mapped_column(Integer, default=100, server_default="100")
    kraever_titel: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    har_loesning: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    kun_staff: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    arkiveret: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class SnPost(Base):
    __tablename__ = "sn_posts"
    __table_args__ = (
        Index("ix_sn_posts_activity", "status", "last_activity_at", "id"),
        Index("ix_sn_posts_cat", "category_id", "status", "last_activity_at", "id"),
        Index("ix_sn_posts_user", "user_id"),
        Index("ix_sn_posts_search", "search", postgresql_using="gin"),
        Index("ix_sn_posts_arter", "arter", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category_id: Mapped[int] = mapped_column(ForeignKey("sn_categories.id"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    eksempel_navn: Mapped[str | None] = mapped_column(String(80))
    titel: Mapped[str | None] = mapped_column(String(200))
    body_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    body_html: Mapped[str] = mapped_column(Text, default="", server_default="")
    body_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    attachment: Mapped[dict | None] = mapped_column(JSONB)
    arter: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list, server_default="{}")
    loknr: Mapped[int | None] = mapped_column(Integer)
    lok_navn: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="synlig", server_default="synlig")  # synlig / skjult / slettet
    hidden_reason: Mapped[str | None] = mapped_column(Text)
    hidden_by: Mapped[int | None] = mapped_column(Integer)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    pinned_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    solution_comment_id: Mapped[int | None] = mapped_column(Integer)  # ingen FK (cirkulær); valideres i service
    n_comments: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    n_reactions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    views: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_activity_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    eksempel: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search = mapped_column(TSVECTOR, Computed(
        "setweight(to_tsvector('danish', coalesce(titel, '')), 'A') || setweight(to_tsvector('danish', coalesce(body_text, '')), 'B')",
        persisted=True))


class SnComment(Base):
    __tablename__ = "sn_comments"
    __table_args__ = (
        Index("ix_sn_comments_post", "post_id", "parent_id", "created_at"),
        Index("ix_sn_comments_user", "user_id"),
        Index("ix_sn_comments_search", "search", postgresql_using="gin"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("sn_posts.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("sn_comments.id", ondelete="CASCADE"))  # altid topniveau
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    eksempel_navn: Mapped[str | None] = mapped_column(String(80))
    reply_to_user_id: Mapped[int | None] = mapped_column(Integer)
    reply_to_navn: Mapped[str | None] = mapped_column(String(80))
    body_md: Mapped[str] = mapped_column(Text, default="", server_default="")
    body_html: Mapped[str] = mapped_column(Text, default="", server_default="")
    body_text: Mapped[str] = mapped_column(Text, default="", server_default="")
    media_id: Mapped[int | None] = mapped_column(ForeignKey("content_media.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(10), default="synlig", server_default="synlig")
    hidden_reason: Mapped[str | None] = mapped_column(String(500))
    hidden_by: Mapped[int | None] = mapped_column(Integer)
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    n_reactions: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    search = mapped_column(TSVECTOR, Computed("to_tsvector('danish', coalesce(body_text, ''))", persisted=True))


class SnReaction(Base):
    """Én reaktion pr. bruger pr. mål (opslag eller kommentar): 👍 ❤️ 😂 😮 😢 😡."""

    __tablename__ = "sn_reactions"
    __table_args__ = (
        UniqueConstraint("user_id", "kind", "target_id"),
        CheckConstraint("kind IN ('post', 'comment')", name="ck_sn_reactions_kind"),
        CheckConstraint("type IN ('like', 'love', 'haha', 'wow', 'sad', 'angry')", name="ck_sn_reactions_type"),
        Index("ix_sn_reactions_target", "kind", "target_id", "type"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(7))
    target_id: Mapped[int] = mapped_column(Integer)
    type: Mapped[str] = mapped_column(String(5))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SnReport(Base):
    __tablename__ = "sn_reports"
    __table_args__ = (UniqueConstraint("kind", "target_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))
    target_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(20))
    note: Mapped[str] = mapped_column(String(500), default="", server_default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    handled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    handled_by: Mapped[int | None] = mapped_column(Integer)


class SnFollow(Base):
    __tablename__ = "sn_follows"

    post_id: Mapped[int] = mapped_column(ForeignKey("sn_posts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class SnView(Base):
    """Visninger én gang pr. bruger pr. døgn."""

    __tablename__ = "sn_views"

    post_id: Mapped[int] = mapped_column(ForeignKey("sn_posts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    dag: Mapped[date] = mapped_column(Date, primary_key=True)
