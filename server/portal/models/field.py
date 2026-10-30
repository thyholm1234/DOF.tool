"""Egne indtastninger (feltmeldinger): ture, observationer, ynglepar og forstyrrelser."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    ARRAY,
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
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class FieldTrip(Base):
    """En tur i portalen, som i DOFbasens indtastning: dato, lokalitet, tider, metode, vejr, GPS-rute og observationer.

    obserkode er observatøren (kan være en anden end brugeren, når der indtastes på vegne af); user_id er den, der
    har indtastet. client_id gør upload fra offline-kø idempotent."""

    __tablename__ = "field_trips"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    obserkode: Mapped[str] = mapped_column(String(16))
    client_id: Mapped[str | None] = mapped_column(String(40))
    dato: Mapped[date] = mapped_column(Date, index=True)
    loknr: Mapped[int] = mapped_column(Integer, index=True)
    turtidfra: Mapped[str | None] = mapped_column(String(5))
    turtidtil: Mapped[str | None] = mapped_column(String(5))
    medobser: Mapped[str | None] = mapped_column(String(300))
    turnoter: Mapped[str | None] = mapped_column(Text)
    metode_id: Mapped[int] = mapped_column(SmallInteger, default=1, server_default="1")
    komplet: Mapped[bool | None] = mapped_column(Boolean)  # komplet artsliste
    spontan: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    temperatur: Mapped[int | None] = mapped_column(SmallInteger)
    skydaekke: Mapped[int | None] = mapped_column(SmallInteger)  # ottendedele
    vindretning: Mapped[str | None] = mapped_column(String(4))
    vindstyrke: Mapped[int | None] = mapped_column(SmallInteger)  # m/s
    nedboer: Mapped[int | None] = mapped_column(SmallInteger)
    nedboertype: Mapped[str | None] = mapped_column(String(8))
    sigtbarhed: Mapped[int | None] = mapped_column(Integer)
    gps_aktiv: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    gps_track: Mapped[list | None] = mapped_column(JSONB)  # [[lat, lon, sekunder siden midnat/epoch, nøjagtighed], ...]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_field_trips_client", "user_id", "client_id", unique=True,
                            postgresql_where=text("client_id IS NOT NULL")),)


class FieldBreedingPair(Base):
    """Ynglepar registreret på en tur (som 'Ny indtastning af ynglepar' i DOFbasen-appen)."""

    __tablename__ = "field_breeding_pairs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("field_trips.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str | None] = mapped_column(String(40))
    artnr: Mapped[int] = mapped_column(Integer)
    antal: Mapped[int] = mapped_column(Integer, default=1)
    adfkode: Mapped[str | None] = mapped_column(String(16))
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)
    noter: Mapped[str | None] = mapped_column(Text)
    hemmelig: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldDisturbance(Base):
    """Forstyrrelse (jagt, hunde, sejlads …) noteret på en tur."""

    __tablename__ = "field_disturbances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trip_id: Mapped[int] = mapped_column(ForeignKey("field_trips.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[str | None] = mapped_column(String(40))
    type_id: Mapped[int] = mapped_column(SmallInteger)
    antal: Mapped[int | None] = mapped_column(Integer)
    noter: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class FieldReport(Base):
    """Én observation indtastet i portalen. Felterne følger DOFbasen; status og sikkerhed er portalens egne."""

    __tablename__ = "field_reports"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # Kun ældre meldinger hører til en tur; nye er én fugl med dato, koordinat og lokalitet
    trip_id: Mapped[int | None] = mapped_column(ForeignKey("field_trips.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    obserkode: Mapped[str] = mapped_column(String(16))
    dato: Mapped[date] = mapped_column(Date, index=True)
    loknr: Mapped[int] = mapped_column(Integer)
    afdelinger: Mapped[list[int]] = mapped_column(ARRAY(SmallInteger), default=list)
    artnr: Mapped[int] = mapped_column(Integer)
    antal: Mapped[int] = mapped_column(Integer, default=1)
    koen: Mapped[str | None] = mapped_column(String(4))
    adfkode: Mapped[str | None] = mapped_column(String(16))
    alderkode: Mapped[str | None] = mapped_column(String(8))
    dragtkode: Mapped[str | None] = mapped_column(String(8))
    metode: Mapped[str | None] = mapped_column(String(80))
    obstidfra: Mapped[str | None] = mapped_column(String(5))
    obstidtil: Mapped[str | None] = mapped_column(String(5))
    obs_lat: Mapped[float | None] = mapped_column(Float)
    obs_lon: Mapped[float | None] = mapped_column(Float)
    radius_m: Mapped[int | None] = mapped_column(Integer)
    fuglnoter: Mapped[str | None] = mapped_column(Text)
    hemmelig: Mapped[bool] = mapped_column(Boolean, default=False)
    skjul_position: Mapped[bool] = mapped_column(Boolean, default=False)
    # Sikkerhed: hvor sikker er bestemmelsen, og hvad bygger den på
    sikkerhed: Mapped[str] = mapped_column(String(12), default="sikker")
    grundlag: Mapped[list[str]] = mapped_column(ARRAY(String(12)), default=list)
    retningslinjer: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    # Under observation: til_stede / fløjet / ikke_genfundet / ukendt (seneste statusopdatering)
    status: Mapped[str] = mapped_column(String(16), default="ukendt")
    status_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    media: Mapped[list] = mapped_column(JSONB, default=list)
    kategori: Mapped[str] = mapped_column(String(8), default="alm")
    antalmetode: Mapped[int | None] = mapped_column(SmallInteger)  # 1 uangivet, 2 præcis, 3 estimat
    maerker: Mapped[list[str]] = mapped_column(ARRAY(String(16)), default=list, server_default="{}")  # kalder, hoert, burfugl …
    client_id: Mapped[str | None] = mapped_column(String(40))  # fra offline-kø: matcher gentagne uploads af samme tur
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_field_reports_thread", "dato", "artnr", "loknr"),)


class FieldReportUpdate(Base):
    """Statusmeldinger på en feltmelding – fra indtasteren eller andre, der står ved fuglen."""

    __tablename__ = "field_report_updates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("field_reports.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(16))
    note: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
