"""Netfugl-showcase: artikler, fotos, arter, RSS, krydslister, roller og API-adgang."""

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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class NfPage(Base):
    """Tekstside i Netfugl-modulet (Om Netfugl, FAQ, Krydsregler …) i let markup."""

    __tablename__ = "nf_pages"

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text, default="")
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfArticle(Base):
    """Nyhed/artikel (kind=artikel) eller kort nyt fra ind- og udland (kind=kort)."""

    __tablename__ = "nf_articles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), default="artikel")
    title: Mapped[str] = mapped_column(String(240))
    body: Mapped[str] = mapped_column(Text, default="")
    writers: Mapped[str | None] = mapped_column(String(300))
    tags: Mapped[list[str]] = mapped_column(ARRAY(String(60)), default=list)
    dato: Mapped[date] = mapped_column(Date, index=True)
    image_url: Mapped[str | None] = mapped_column(String(400))
    source_url: Mapped[str | None] = mapped_column(String(400))
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfComment(Base):
    """Kommentar til en artikel eller et foto."""

    __tablename__ = "nf_comments"
    __table_args__ = (Index("ix_nf_comments_target", "target_kind", "target_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(8))  # artikel / foto
    target_id: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text)
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)


class NfPhoto(Base):
    """Foto i galleriet. Kan knyttes til en observation i DOFbasen (obsid). Nye fotos kan kræve godkendelse."""

    __tablename__ = "nf_photos"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    url: Mapped[str] = mapped_column(String(400))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    artnr: Mapped[int | None] = mapped_column(Integer, index=True)
    art_navn: Mapped[str] = mapped_column(String(200))
    latin: Mapped[str | None] = mapped_column(String(200))
    dato: Mapped[date | None] = mapped_column(Date)
    lokalitet: Mapped[str | None] = mapped_column(String(250))
    loknr: Mapped[int | None] = mapped_column(Integer)
    land: Mapped[str] = mapped_column(String(80), default="Danmark")
    alder: Mapped[str | None] = mapped_column(String(40))
    beskrivelse: Mapped[str | None] = mapped_column(Text)
    obsid: Mapped[int | None] = mapped_column(BigInteger, index=True)
    status: Mapped[str] = mapped_column(String(12), default="publiceret")  # afventer / publiceret / afvist
    views: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)


class NfLike(Base):
    __tablename__ = "nf_likes"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    target_kind: Mapped[str] = mapped_column(String(8), primary_key=True)
    target_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfSpecies(Base):
    """Arter på DK-listen og VP-listen. artnr kobler til DOFbasens art (via latinsk navn)."""

    __tablename__ = "nf_species"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    liste: Mapped[str] = mapped_column(String(4), index=True)  # dk / vp
    sort: Mapped[int] = mapped_column(Integer, default=0)
    su: Mapped[bool] = mapped_column(Boolean, default=False)
    navn: Mapped[str] = mapped_column(String(200))
    latin: Mapped[str] = mapped_column(String(200))
    engelsk: Mapped[str | None] = mapped_column(String(200))
    kategori: Mapped[str | None] = mapped_column(String(4))
    artnr: Mapped[int | None] = mapped_column(Integer)


class NfFamily(Base):
    __tablename__ = "nf_families"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    orden: Mapped[str] = mapped_column(String(60))
    familie: Mapped[str] = mapped_column(String(80))
    engelsk: Mapped[str] = mapped_column(String(160))
    antal: Mapped[int] = mapped_column(Integer, default=0)


class NfLink(Base):
    __tablename__ = "nf_links"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    gruppe: Mapped[str] = mapped_column(String(60))
    titel: Mapped[str] = mapped_column(String(160))
    url: Mapped[str] = mapped_column(String(400))
    sort: Mapped[int] = mapped_column(Integer, default=0)


class NfFeed(Base):
    """RSS-kilde til Netfugl live. feed_url findes automatisk fra site_url (<link rel=alternate>), hvis den mangler."""

    __tablename__ = "nf_feeds"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    navn: Mapped[str] = mapped_column(String(160))
    site_url: Mapped[str | None] = mapped_column(String(400))
    feed_url: Mapped[str | None] = mapped_column(String(400))
    land: Mapped[str] = mapped_column(String(60), default="Danmark")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_fetch: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(String(400))
    items: Mapped[int] = mapped_column(Integer, default=0)


class NfFeedItem(Base):
    __tablename__ = "nf_feed_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("nf_feeds.id", ondelete="CASCADE"), index=True)
    guid: Mapped[str] = mapped_column(String(400))
    title: Mapped[str] = mapped_column(String(400))
    url: Mapped[str | None] = mapped_column(String(600))
    summary: Mapped[str | None] = mapped_column(Text)
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    __table_args__ = (UniqueConstraint("feed_id", "guid"),)


class NfKryds(Base):
    """Manuelt kryds på en krydsliste (supplerer de automatiske fra DOFbasen). hidden=True skjuler et automatisk kryds."""

    __tablename__ = "nf_kryds"
    __table_args__ = (Index("ix_nf_kryds_list", "liste", "obserkode"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    obserkode: Mapped[str] = mapped_column(String(16), index=True)
    liste: Mapped[str] = mapped_column(String(24))  # dk / vp / falskvp / ring / aedel
    artnr: Mapped[int | None] = mapped_column(Integer)
    latin: Mapped[str] = mapped_column(String(200))
    navn: Mapped[str] = mapped_column(String(200))
    dato: Mapped[date | None] = mapped_column(Date)
    lokalitet: Mapped[str | None] = mapped_column(String(250))
    land: Mapped[str | None] = mapped_column(String(80))
    note: Mapped[str | None] = mapped_column(String(500))
    hidden: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfAutoList(Base):
    """Automatiske kryds beregnet af workeren ud fra DOFbasen-observationer: første fund pr. art på én deltagers liste.
    Én række pr. deltager og liste med parallelle arrays (element i = én art) – ikke én række pr. kryds, som kostede en
    rækkeheader og en indekspost pr. kryds; store arrays komprimeres desuden af Postgres."""

    __tablename__ = "nf_auto_lists"

    obserkode: Mapped[str] = mapped_column(String(16), primary_key=True)
    liste: Mapped[str] = mapped_column(String(24), primary_key=True)  # dk / aar:2026 / md:1 / afd:574 / kom:0101
    artnr: Mapped[list[int]] = mapped_column(ARRAY(Integer))
    dato: Mapped[list[date]] = mapped_column(ARRAY(Date))
    obsid: Mapped[list[int | None]] = mapped_column(ARRAY(BigInteger))
    loknr: Mapped[list[int | None]] = mapped_column(ARRAY(Integer))


class NfRole(Base):
    """Netfugl-modulets egne roller og rettigheder (adskilt fra portalens og DOFbasens roller)."""

    __tablename__ = "nf_roles"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    perms: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    sort: Mapped[int] = mapped_column(Integer, default=100)


class NfUserRole(Base):
    __tablename__ = "nf_user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_key: Mapped[str] = mapped_column(ForeignKey("nf_roles.key", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class NfApiGrant(Base):
    __tablename__ = "nf_api_grants"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rate_per_min: Mapped[int] = mapped_column(Integer, default=60)
    max_tokens: Mapped[int] = mapped_column(Integer, default=5)
    note: Mapped[str | None] = mapped_column(String(300))


class NfApiToken(Base):
    __tablename__ = "nf_api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses: Mapped[int] = mapped_column(BigInteger, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class NfAudit(Base):
    __tablename__ = "nf_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
