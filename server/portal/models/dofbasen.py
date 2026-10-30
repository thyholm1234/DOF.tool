"""DOFbasen-showcase: sider, nyheder, lister, roller, API-adgang og DKU-status."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class DofbPage(Base):
    """Tekstside i DOFbasen-modulet (Hvad er det?, Rettigheder, Hjælp …). Sektioner med let markup, redigeres af redaktører."""

    __tablename__ = "dofb_pages"

    slug: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    sections: Mapped[list] = mapped_column(JSONB, default=list)  # [{title, body}]
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbNews(Base):
    """Nyt på DOFbasen."""

    __tablename__ = "dofb_news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dato: Mapped[date] = mapped_column(Date, index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    published: Mapped[bool] = mapped_column(Boolean, default=True)
    pinned: Mapped[bool] = mapped_column(Boolean, default=False)  # vises under listen (fx "Kendte fejl")
    author_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbFocus(Base):
    """Forsidens FOKUS-bokse (temaer); de aktive vises som faneblade."""

    __tablename__ = "dofb_focus"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    body: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=100)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbList(Base):
    """Redigerbar opslagsliste (SU-arter, adfærdskoder, links …): kolonner og rækker som tekst."""

    __tablename__ = "dofb_lists"

    key: Mapped[str] = mapped_column(String(40), primary_key=True)
    title: Mapped[str] = mapped_column(String(160))
    intro: Mapped[str] = mapped_column(Text, default="")
    columns: Mapped[list] = mapped_column(JSONB, default=list)
    rows: Mapped[list] = mapped_column(JSONB, default=list)
    updated_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbArea(Base):
    """Storlokalitet eller IBA-område: en navngiven samling DOFbasen-lokaliteter (bruges i søgning og lister)."""

    __tablename__ = "dofb_areas"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(8), index=True)  # stor / iba
    dof_id: Mapped[int | None] = mapped_column(Integer)  # nummeret på dofbasen.dk (storlokal=…)
    navn: Mapped[str] = mapped_column(String(200))
    loknrs: Mapped[list[int]] = mapped_column(ARRAY(Integer), default=list)


class DofbRole(Base):
    """DOFbasen-modulets egne roller (adskilt fra portalens roller) og deres rettigheder."""

    __tablename__ = "dofb_roles"

    key: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    perms: Mapped[list[str]] = mapped_column(ARRAY(String(32)), default=list)
    sort: Mapped[int] = mapped_column(Integer, default=100)


class DofbUserRole(Base):
    __tablename__ = "dofb_user_roles"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role_key: Mapped[str] = mapped_column(ForeignKey("dofb_roles.key", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbApiGrant(Base):
    """Adgang til DOFbasen-API'et, tildelt af en administrator. Brugeren laver selv sine tokens."""

    __tablename__ = "dofb_api_grants"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    granted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    rate_per_min: Mapped[int] = mapped_column(Integer, default=60)
    max_tokens: Mapped[int] = mapped_column(Integer, default=5)
    note: Mapped[str | None] = mapped_column(String(300))


class DofbApiToken(Base):
    """Personligt API-token. Kun en SHA-256 af tokenet gemmes; selve tokenet vises én gang ved oprettelse."""

    __tablename__ = "dofb_api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    prefix: Mapped[str] = mapped_column(String(16))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    uses: Mapped[int] = mapped_column(BigInteger, default=0)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DofbObsStatus(Base):
    """Kvalitetssikring (DKU) af en observation: til behandling (blå pil), behandlet af DKU eller godkendt af SU."""

    __tablename__ = "dofb_obs_status"

    obsid: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    status: Mapped[str] = mapped_column(String(12))  # behandling / dku / su
    note: Mapped[str | None] = mapped_column(Text)
    set_by: Mapped[int | None] = mapped_column(Integer)  # users.id (ingen fremmednøgle: tabellen ligger i observationsgruppen)
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DofbAudit(Base):
    """Log over ændringer i DOFbasen-modulet (vises i admin-panelets oversigt)."""

    __tablename__ = "dofb_audit"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    action: Mapped[str] = mapped_column(String(40))
    target: Mapped[str | None] = mapped_column(String(200))
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
