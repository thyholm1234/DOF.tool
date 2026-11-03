"""Læring: kurser, spørgsmål, fremgang og quizresultater."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    Index,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from portal.db import Base


class Course(Base):
    __tablename__ = "courses"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text, default="")
    # id / kald / sæson / andet
    topic: Mapped[str] = mapped_column(String(16), default="id")
    level: Mapped[str] = mapped_column(String(16), default="øvet")
    intro: Mapped[str] = mapped_column(Text, default="")
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Læring 2.0 (docs/laering-spec.md §3.3): kurser og øvelser i samme tabel
    kind: Mapped[str] = mapped_column(String(8), default="kursus", server_default="kursus")  # kursus / ovelse / quiz / spil
    number: Mapped[int | None] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(40), default="", server_default="")
    category: Mapped[str] = mapped_column(String(40), default="", server_default="")
    card: Mapped[str] = mapped_column(Text, default="", server_default="")
    audience: Mapped[str] = mapped_column(Text, default="", server_default="")
    learn: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, server_default="{}")
    hours: Mapped[str] = mapped_column(String(20), default="", server_default="")
    hue: Mapped[str] = mapped_column(String(16), default="eng", server_default="eng")
    sil: Mapped[str] = mapped_column(String(16), default="spurvefugl", server_default="spurvefugl")
    cover_media_id: Mapped[int | None] = mapped_column(Integer)
    featured: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    visibility: Mapped[str] = mapped_column(String(8), default="alle", server_default="alle")  # alle / login / hold
    version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    published_snapshot: Mapped[dict | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[int | None] = mapped_column(Integer)


class Unit(Base):
    """En lektion i et kursus: læsetekst, indholdsblokke og aktiviteter (questions.unit_id)."""

    __tablename__ = "lr_units"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    title: Mapped[str] = mapped_column(String(160), default="")
    lesson_md: Mapped[str] = mapped_column(Text, default="")
    blocks: Mapped[list] = mapped_column(JSONB, default=list, server_default="[]")
    min_score_pct: Mapped[int | None] = mapped_column(SmallInteger)
    est_minutes: Mapped[int | None] = mapped_column(SmallInteger)


class Question(Base):
    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    prompt: Mapped[str] = mapped_column(Text)
    media_kind: Mapped[str | None] = mapped_column(String(8))  # image / audio
    media_url: Mapped[str | None] = mapped_column(String(400))
    media_credit: Mapped[str | None] = mapped_column(String(200))
    # [{"text": "...", "correct": true}, ...]
    choices: Mapped[list] = mapped_column(JSONB)
    explanation: Mapped[str] = mapped_column(Text, default="")
    # Læring 2.0: aktivitetstyper (spec §5.2)
    unit_id: Mapped[int | None] = mapped_column(ForeignKey("lr_units.id", ondelete="SET NULL"), index=True)
    type: Mapped[str] = mapped_column(String(16), default="valg", server_default="valg")
    config: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")
    accept: Mapped[list[str]] = mapped_column(ARRAY(String(200)), default=list, server_default="{}")
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, server_default="{}")
    media_photo_id: Mapped[int | None] = mapped_column(Integer)
    media_rec_id: Mapped[int | None] = mapped_column(Integer)
    ask_confidence: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")


class QuestionProgress(Base):
    """Spaced repetition (Leitner-kasser) pr. bruger og spørgsmål."""

    __tablename__ = "question_progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    question_id: Mapped[int] = mapped_column(ForeignKey("questions.id", ondelete="CASCADE"), primary_key=True)
    box: Mapped[int] = mapped_column(SmallInteger, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    wrong: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuizResult(Base):
    __tablename__ = "quiz_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    quiz_key: Mapped[str] = mapped_column(String(80))
    score: Mapped[int] = mapped_column(Integer)
    total: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class SoundRecording(Base):
    """Lydoptagelse til lydquizzen. xeno-canto-optagelser findes automatisk pr. art (API) og gemmes her; Macaulay Library-optagelser
    lægges ind af redaktører (ML har intet åbent API)."""

    __tablename__ = "snd_recordings"
    __table_args__ = (UniqueConstraint("source", "ext_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(4))  # xc / ml
    ext_id: Mapped[str] = mapped_column(String(20))  # XC-nummer eller ML-katalognummer
    artnr: Mapped[int] = mapped_column(Integer, index=True)
    latin: Mapped[str | None] = mapped_column(String(200))
    recordist: Mapped[str | None] = mapped_column(String(200))
    license: Mapped[str | None] = mapped_column(String(200))
    license_url: Mapped[str | None] = mapped_column(String(300))
    rtype: Mapped[str | None] = mapped_column(String(80))  # sang, kald …
    quality: Mapped[str | None] = mapped_column(String(2))
    length_s: Mapped[int | None] = mapped_column(Integer)
    country: Mapped[str | None] = mapped_column(String(80))
    page_url: Mapped[str] = mapped_column(String(300))
    file_url: Mapped[str] = mapped_column(String(400))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    added_by: Mapped[int | None] = mapped_column(Integer)  # users.id (ML-optagelser)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LrPath(Base):
    """Forløb: en rækkefølge af kurser, øvelser, træninger og feltopgaver i sektioner."""

    __tablename__ = "lr_paths"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), unique=True)
    title: Mapped[str] = mapped_column(String(160))
    summary: Mapped[str] = mapped_column(Text, default="")
    intro_md: Mapped[str] = mapped_column(Text, default="")
    level: Mapped[str] = mapped_column(String(16), default="begynder")
    audience: Mapped[str] = mapped_column(Text, default="")
    cover_media_id: Mapped[int | None] = mapped_column(Integer)
    published: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility: Mapped[str] = mapped_column(String(8), default="alle")  # alle / login / hold
    certificate: Mapped[bool] = mapped_column(Boolean, default=False)
    certificate_text: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LrPathItem(Base):
    __tablename__ = "lr_path_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path_id: Mapped[int] = mapped_column(ForeignKey("lr_paths.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    section: Mapped[str] = mapped_column(String(80), default="")
    kind: Mapped[str] = mapped_column(String(10))  # kursus / ovelse / felt / tekst / traening
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="SET NULL"))
    field_task: Mapped[dict | None] = mapped_column(JSONB)
    text_md: Mapped[str | None] = mapped_column(Text)
    training: Mapped[dict | None] = mapped_column(JSONB)
    title: Mapped[str] = mapped_column(String(160), default="")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    min_score_pct: Mapped[int | None] = mapped_column(SmallInteger)
    unlock: Mapped[dict] = mapped_column(JSONB, default=dict, server_default="{}")


class LrCohort(Base):
    __tablename__ = "lr_cohorts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    path_id: Mapped[int] = mapped_column(ForeignKey("lr_paths.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    afdeling_id: Mapped[int | None] = mapped_column(Integer)
    starts_on: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    ends_on: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    join_code: Mapped[str] = mapped_column(String(12), unique=True)
    open_join: Mapped[bool] = mapped_column(Boolean, default=False)
    max_members: Mapped[int | None] = mapped_column(Integer)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    archived: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")


class LrCohortMember(Base):
    __tablename__ = "lr_cohort_members"

    cohort_id: Mapped[int] = mapped_column(ForeignKey("lr_cohorts.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10), default="deltager")  # deltager / underviser
    joined_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LrEnrollment(Base):
    __tablename__ = "lr_enrollments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    path_id: Mapped[int | None] = mapped_column(ForeignKey("lr_paths.id", ondelete="CASCADE"))
    course_id: Mapped[int | None] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"))
    cohort_id: Mapped[int | None] = mapped_column(ForeignKey("lr_cohorts.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    certificate_no: Mapped[str | None] = mapped_column(String(16), unique=True)


class LrUnitProgress(Base):
    __tablename__ = "lr_unit_progress"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    unit_id: Mapped[int] = mapped_column(ForeignKey("lr_units.id", ondelete="CASCADE"), primary_key=True)
    best_score: Mapped[int] = mapped_column(Integer, default=0)
    total: Mapped[int] = mapped_column(Integer, default=0)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LrAnswer(Base):
    """Svarlog: grundlag for færdigheder, artsmestring og kalibrering."""

    __tablename__ = "lr_answers"
    __table_args__ = (Index("ix_lr_answers_user_time", "user_id", "created_at"), Index("ix_lr_answers_user_art", "user_id", "artnr", "modality"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    source: Mapped[str] = mapped_column(String(10))  # kursus / ovelse / arter / lyd / obs / forbered / min
    question_id: Mapped[int | None] = mapped_column(Integer)
    artnr: Mapped[int | None] = mapped_column(Integer)
    modality: Mapped[str] = mapped_column(String(8), default="tekst")  # foto / lyd / tekst / data / silhuet
    correct: Mapped[bool] = mapped_column(Boolean)
    confidence: Mapped[int | None] = mapped_column(SmallInteger)
    skills: Mapped[list[str]] = mapped_column(ARRAY(String(20)), default=list, server_default="{}")
    ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LrSpeciesMastery(Base):
    __tablename__ = "lr_species_mastery"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    artnr: Mapped[int] = mapped_column(Integer, primary_key=True)
    modality: Mapped[str] = mapped_column(String(8), primary_key=True)  # foto / lyd
    box: Mapped[int] = mapped_column(SmallInteger, default=0)
    correct: Mapped[int] = mapped_column(Integer, default=0)
    wrong: Mapped[int] = mapped_column(Integer, default=0)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class LrTraining(Base):
    __tablename__ = "lr_trainings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(160))
    config: Mapped[dict] = mapped_column(JSONB)
    share_token: Mapped[str | None] = mapped_column(String(16), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class LrFieldDone(Base):
    __tablename__ = "lr_field_done"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    path_item_id: Mapped[int] = mapped_column(ForeignKey("lr_path_items.id", ondelete="CASCADE"), primary_key=True)
    cohort_id: Mapped[int] = mapped_column(Integer, primary_key=True, default=0)  # 0 = uden hold
    obsid: Mapped[int | None] = mapped_column(BigInteger)
    done_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    how: Mapped[str] = mapped_column(String(10), default="obs")  # obs / selv / underviser
    approved: Mapped[bool] = mapped_column(Boolean, default=True)
    note: Mapped[str | None] = mapped_column(Text)


class LrCourseEditor(Base):
    __tablename__ = "lr_course_editors"

    course_id: Mapped[int] = mapped_column(ForeignKey("courses.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)


class LrGoal(Base):
    __tablename__ = "lr_goals"

    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    n_species: Mapped[int] = mapped_column(Integer)
    modality: Mapped[str] = mapped_column(String(8), default="lyd")
    deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
