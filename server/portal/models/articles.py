"""Artikelmodulet: artikler (netfugl-arkivet og nye), emneord, arter, medier, kommentarer, abonnementer og importer."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Computed,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base

# Fuldtekstvektor: titel vejer mest, OCR-tekst mindst (se docs/artikler-modul-spec.md §5.1)
SEARCH_EXPR = (
    "setweight(to_tsvector('danish', coalesce(title, '')), 'A') || "
    "setweight(to_tsvector('danish', coalesce(lead, '')), 'B') || "
    "setweight(to_tsvector('danish', coalesce(body_text, '')), 'C') || "
    "setweight(to_tsvector('danish', coalesce(ocr_text, '')), 'D')"
)


class ArtImport(Base):
    """Én uploadet zip med artikelarkivet og dens importrapport."""

    __tablename__ = "art_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    filename: Mapped[str] = mapped_column(String(300))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(12), default="uploader")  # uploader / klar / koerer / faerdig / fejl
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    log: Mapped[list] = mapped_column(JSONB, default=list)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ContentMedia(Base):
    """Billeder, PDF'er og filer til artikler og nyheder. owner_id er null, mens en ny artikel endnu ikke er gemt."""

    __tablename__ = "content_media"
    __table_args__ = (Index("ix_content_media_owner", "owner_kind", "owner_id"), Index("ix_content_media_sha", "sha256"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner_kind: Mapped[str] = mapped_column(String(10))  # artikel / nyhed
    owner_id: Mapped[int | None] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(10))  # billede / pdf / fil
    storage_key: Mapped[str] = mapped_column(String(200))
    thumb_key: Mapped[str | None] = mapped_column(String(200))
    medium_key: Mapped[str | None] = mapped_column(String(200))
    filename: Mapped[str] = mapped_column(String(300))
    mime: Mapped[str] = mapped_column(String(100))
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    alt: Mapped[str] = mapped_column(String(300), default="")
    caption: Mapped[str] = mapped_column(String(300), default="")
    credit: Mapped[str] = mapped_column(String(160), default="")
    original_url: Mapped[str | None] = mapped_column(String(500))
    pages: Mapped[int | None] = mapped_column(Integer)
    text_md: Mapped[str | None] = mapped_column(Text)  # OCR-tekst fra en PDF
    text_source: Mapped[str | None] = mapped_column(String(16))
    sha256: Mapped[str] = mapped_column(String(64))
    sort: Mapped[int] = mapped_column(Integer, default=0)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtArticle(Base):
    __tablename__ = "art_articles"
    __table_args__ = (
        Index("ix_art_articles_search", "search", postgresql_using="gin"),
        Index("ix_art_articles_status_dato", "status", "dato"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    netfugl_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    kind: Mapped[str] = mapped_column(String(12), default="artikel")  # artikel / pdf / (turrapport)
    title: Mapped[str] = mapped_column(String(300))
    slug: Mapped[str] = mapped_column(String(120), default="")
    lead: Mapped[str | None] = mapped_column(Text)
    body_md: Mapped[str] = mapped_column(Text, default="")
    body_html: Mapped[str] = mapped_column(Text, default="")
    body_text: Mapped[str] = mapped_column(Text, default="")
    ocr_text: Mapped[str | None] = mapped_column(Text)
    render_version: Mapped[int] = mapped_column(Integer, default=0)
    dato: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    authors: Mapped[list] = mapped_column(JSONB, default=list)
    uploaded_by: Mapped[dict | None] = mapped_column(JSONB)
    last_edited_netfugl: Mapped[dict | None] = mapped_column(JSONB)
    cover_media_id: Mapped[int | None] = mapped_column(ForeignKey("content_media.id", ondelete="SET NULL"))
    cover_caption: Mapped[str | None] = mapped_column(String(300))
    status: Mapped[str] = mapped_column(String(12), default="publiceret")  # kladde / planlagt / publiceret / skjult
    publish_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    featured_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    comments_open: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    source: Mapped[str] = mapped_column(String(10), default="lokal")  # netfugl / lokal
    source_url: Mapped[str | None] = mapped_column(String(400))
    views_imported: Mapped[int] = mapped_column(Integer, default=0)
    views: Mapped[int] = mapped_column(Integer, default=0)
    locally_edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    version: Mapped[int] = mapped_column(Integer, default=1)
    import_id: Mapped[int | None] = mapped_column(ForeignKey("art_imports.id", ondelete="SET NULL"))
    import_errors: Mapped[list] = mapped_column(JSONB, default=list)
    source_hash: Mapped[str | None] = mapped_column(String(40))  # fingeraftryk af arkivets filer (springer uændrede artikler over)
    search = mapped_column(TSVECTOR, Computed(SEARCH_EXPR, persisted=True))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtTag(Base):
    __tablename__ = "art_tags"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(120))
    navn_lc: Mapped[str] = mapped_column(String(120), unique=True)
    netfugl_tag_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    kind: Mapped[str] = mapped_column(String(12), default="emne")  # type / publikation / aar / emne / sted / art
    artnr: Mapped[int | None] = mapped_column(Integer)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class ArtArticleTag(Base):
    __tablename__ = "art_article_tags"
    __table_args__ = (Index("ix_art_article_tags_tag", "tag_id", "article_id"),)

    article_id: Mapped[int] = mapped_column(ForeignKey("art_articles.id", ondelete="CASCADE"), primary_key=True)
    tag_id: Mapped[int] = mapped_column(ForeignKey("art_tags.id", ondelete="CASCADE"), primary_key=True)


class ArtSpecies(Base):
    """Arter, en artikel handler om. source: emneord / tekst / ocr / redaktoer / fravalgt (redaktøren har fjernet den)."""

    __tablename__ = "art_species"
    __table_args__ = (Index("ix_art_species_art", "artnr", "article_id"),)

    article_id: Mapped[int] = mapped_column(ForeignKey("art_articles.id", ondelete="CASCADE"), primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(10), default="tekst")
    hits: Mapped[int] = mapped_column(Integer, default=1)
    first_pos: Mapped[int | None] = mapped_column(Integer)


class ArtComment(Base):
    """Kommentar til en artikel. user_id er null for kommentarer fra netfugl.dk (arkiv, kun til læsning)."""

    __tablename__ = "art_comments"
    __table_args__ = (Index("ix_art_comments_article", "article_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("art_articles.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("art_comments.id", ondelete="CASCADE"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    archive_author: Mapped[str | None] = mapped_column(String(160))
    archive_netfugl_user: Mapped[int | None] = mapped_column(Integer)
    archive_key: Mapped[str | None] = mapped_column(String(40), unique=True)
    body_md: Mapped[str] = mapped_column(Text)
    body_html: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(10), default="synlig")  # synlig / skjult / slettet
    hidden_reason: Mapped[str | None] = mapped_column(String(500))
    hidden_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    report_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtCommentReport(Base):
    __tablename__ = "art_comment_reports"
    __table_args__ = (UniqueConstraint("comment_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("art_comments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[str | None] = mapped_column(String(10))  # skjult / afvist


class ArtSubscription(Base):
    """Abonnement: alle nye artikler (kind=alle, ref=0), kommentarer på en artikel (artikel), senere emneord og arter.
    muted=true betyder «følg ikke» og forhindrer, at en ny kommentar tilmelder igen."""

    __tablename__ = "art_subscriptions"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10), primary_key=True)
    ref: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)
    via: Mapped[str] = mapped_column(String(10), default="manuel")  # manuel / kommentar
    muted: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ArtRevision(Base):
    __tablename__ = "art_revisions"
    __table_args__ = (Index("ix_art_revisions_article", "article_id", "version"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    article_id: Mapped[int] = mapped_column(ForeignKey("art_articles.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    title: Mapped[str] = mapped_column(String(300))
    body_md: Mapped[str] = mapped_column(Text)
    meta: Mapped[dict] = mapped_column(JSONB, default=dict)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

