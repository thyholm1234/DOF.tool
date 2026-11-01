"""Fotomodulet: fotos, fugle på fotos, kodelister, fotografer, grupper, kommentarer, likes, privilegier, bannere og importer.

Alt er brugerdata (se dbgroups.USER_TABLES). Arter slås op i ``species`` (referencedata). Ingen SQL må joine ``gal_*`` med
``observations``: koblingen til DOFbasen går via ``gal_photos.obsid`` og to forespørgsler (docs/netfugl-afloesning-spec.md §8)."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
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
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class GalCountry(Base):
    """Netfugls «lande» (inkl. fx «Rusland (indenfor VP)») bevares, som de er."""

    __tablename__ = "gal_countries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(120))
    netfugl_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    iso2: Mapped[str | None] = mapped_column(String(2))
    vp: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class GalCode(Base):
    """Kodelister: kind ∈ adfaerd / koen / alder / dragt. dofbasen_code kobler til code_texts (adf/dragt) eller observationens alderkode."""

    __tablename__ = "gal_codes"

    kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    code: Mapped[str] = mapped_column(String(16), primary_key=True)
    text: Mapped[str] = mapped_column(String(120))
    sort: Mapped[int] = mapped_column(Integer, default=100)
    dofbasen_code: Mapped[str | None] = mapped_column(String(16))


class GalPhotographer(Base):
    """Én pr. fotograf, også dem uden bruger. match: auto / manuel / ingen / tvetydig (docs/netfugl-afloesning-spec.md §5)."""

    __tablename__ = "gal_photographers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(200))
    navn_norm: Mapped[str] = mapped_column(String(200), index=True)
    netfugl_bruger_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), unique=True)
    obserkode: Mapped[str | None] = mapped_column(String(16), index=True)
    match: Mapped[str] = mapped_column(String(10), default="ingen", server_default="ingen")
    standard_rettighed: Mapped[int] = mapped_column(SmallInteger, default=2, server_default="2")
    dof_samtykke: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GalGroup(Base):
    __tablename__ = "gal_groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    photographer_id: Mapped[int | None] = mapped_column(ForeignKey("gal_photographers.id", ondelete="SET NULL"))
    artnr: Mapped[int | None] = mapped_column(Integer)
    dato: Mapped[date | None] = mapped_column(Date)
    cover_photo_id: Mapped[int | None] = mapped_column(Integer)  # uden fremmednøgle: gal_photos peger på gruppen


class GalPhoto(Base):
    __tablename__ = "gal_photos"
    __table_args__ = (
        Index("ix_gal_photos_status_pub", "status", "published_at"),
        Index("ix_gal_photos_status_dato", "status", "dato"),
        Index("ix_gal_photos_user", "user_id"),
        Index("ix_gal_photos_photographer", "photographer_id"),
        Index("ix_gal_photos_land", "land_id"),
        Index("ix_gal_photos_group", "group_id"),
        Index("ix_gal_photos_obsid", "obsid"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    netfugl_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    photographer_id: Mapped[int | None] = mapped_column(ForeignKey("gal_photographers.id", ondelete="SET NULL"))
    media_id: Mapped[int | None] = mapped_column(ForeignKey("content_media.id", ondelete="SET NULL"))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    titel: Mapped[str | None] = mapped_column(String(200))
    beskrivelse_md: Mapped[str | None] = mapped_column(Text)
    beskrivelse_html: Mapped[str | None] = mapped_column(Text)
    dato: Mapped[date | None] = mapped_column(Date)
    land_id: Mapped[int | None] = mapped_column(ForeignKey("gal_countries.id", ondelete="SET NULL"))
    lokalitet: Mapped[str | None] = mapped_column(String(250))
    loknr: Mapped[int | None] = mapped_column(Integer)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(24)), default=list, server_default="{}")  # personer / historisk / tvivl_art / tvivl_kad
    kamera: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    rettighed: Mapped[int | None] = mapped_column(SmallInteger)  # 1–3; null = fotografens standard
    status: Mapped[str] = mapped_column(String(12), default="afventer")  # afventer / publiceret / afvist / afpubliceret
    afvist_grund: Mapped[str | None] = mapped_column(Text)
    behandlet_af: Mapped[int | None] = mapped_column(Integer)
    behandlet_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    locked_by: Mapped[int | None] = mapped_column(Integer)
    locked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    group_id: Mapped[int | None] = mapped_column(ForeignKey("gal_groups.id", ondelete="SET NULL"))
    obsid: Mapped[int | None] = mapped_column(BigInteger)  # egen DOFbasen-observation (ingen fremmednøgle: anden databasegruppe)
    likes: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    views: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    comments: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    views_imported: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    likes_imported: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    source: Mapped[str] = mapped_column(String(12), default="upload")  # netfugl / upload
    source_hash: Mapped[str | None] = mapped_column(String(64))
    locally_edited: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GalBird(Base):
    """En fugl på et foto (1–10). pos 0 er hovedarten. taxon_id peger på kl_taxa (World Bird List) uden fremmednøgle."""

    __tablename__ = "gal_birds"
    __table_args__ = (Index("ix_gal_birds_art", "artnr", "photo_id"), Index("ix_gal_birds_taxon", "taxon_id"),
                      Index("ix_gal_birds_photo", "photo_id", "pos"))

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("gal_photos.id", ondelete="CASCADE"))
    pos: Mapped[int] = mapped_column(SmallInteger, default=0)
    artnr: Mapped[int | None] = mapped_column(Integer)
    taxon_id: Mapped[int | None] = mapped_column(Integer)
    netfugl_art_id: Mapped[int | None] = mapped_column(Integer)
    navn: Mapped[str] = mapped_column(String(200))
    latin: Mapped[str | None] = mapped_column(String(200))
    adfaerd: Mapped[str | None] = mapped_column(String(16))
    koen: Mapped[str | None] = mapped_column(String(16))
    alder: Mapped[str | None] = mapped_column(String(16))
    dragt: Mapped[str | None] = mapped_column(String(16))


class GalComment(Base):
    """Kommentar til et foto (som art_comments). user_id er null for kommentarer fra netfugl.dk. intern = moderatorernes noter."""

    __tablename__ = "gal_comments"
    __table_args__ = (Index("ix_gal_comments_photo", "photo_id", "created_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("gal_photos.id", ondelete="CASCADE"))
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("gal_comments.id", ondelete="CASCADE"))
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
    intern: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GalCommentReport(Base):
    __tablename__ = "gal_comment_reports"
    __table_args__ = (UniqueConstraint("comment_id", "user_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    comment_id: Mapped[int] = mapped_column(ForeignKey("gal_comments.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    reason: Mapped[str] = mapped_column(String(40))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[str | None] = mapped_column(String(10))


class GalLike(Base):
    __tablename__ = "gal_likes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("gal_photos.id", ondelete="CASCADE"), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GalPrivilege(Base):
    """Uploadprivilegium: standard / maanedlig / ubegraenset / udelukket. Ingen række = standard."""

    __tablename__ = "gal_privileges"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    niveau: Mapped[str] = mapped_column(String(12), default="standard")
    note: Mapped[str | None] = mapped_column(String(300))
    set_by: Mapped[int | None] = mapped_column(Integer)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class GalBanner(Base):
    __tablename__ = "gal_banners"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    photo_id: Mapped[int] = mapped_column(ForeignKey("gal_photos.id", ondelete="CASCADE"))
    media_id: Mapped[int | None] = mapped_column(ForeignKey("content_media.id", ondelete="SET NULL"))
    crop: Mapped[dict] = mapped_column(JSONB, default=dict)  # {x, y, w, h} som brøkdele 0–1
    tekst: Mapped[str | None] = mapped_column(String(200))
    fra: Mapped[date | None] = mapped_column(Date)
    til: Mapped[date | None] = mapped_column(Date)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class GalImport(Base):
    __tablename__ = "gal_imports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kilde: Mapped[str] = mapped_column(String(8), default="zip")  # zip / mappe
    sti: Mapped[str | None] = mapped_column(String(300))
    filename: Mapped[str] = mapped_column(String(300), default="")
    size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(12), default="uploader")  # uploader / klar / koerer / faerdig / fejl
    options: Mapped[dict] = mapped_column(JSONB, default=dict)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict)
    log: Mapped[list] = mapped_column(JSONB, default=list)
    uploaded_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
