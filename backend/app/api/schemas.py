from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    postgres: str
    redis: str


class DofLoginRequest(BaseModel):
    obserkode: str = Field(min_length=2, max_length=16)
    adgangskode: str = Field(min_length=1)


class SessionUser(BaseModel):
    obserkode: str
    navn: str
    is_admin: bool
    environment: str


class ObservationAlert(BaseModel):
    obsid: str
    species: str
    category: str
    location: str
    count: int | None = None
    observed_at: datetime


class YearRankingRow(BaseModel):
    year: int
    rank: int
    observer_name: str
    species_count: int


class TrendSignal(BaseModel):
    species: str
    trend_score: float
    recent_count: int
    baseline_count: int
    note: str


class TripAnnouncementCreate(BaseModel):
    title: str = Field(min_length=3, max_length=140)
    location: str = Field(min_length=2, max_length=140)
    starts_at: datetime
    max_participants: int = Field(ge=1, le=30)
    description: str = Field(min_length=10, max_length=1000)


class TripAnnouncementRead(TripAnnouncementCreate):
    id: str
    created_by: str
    joined_count: int


class TripJoinRequest(BaseModel):
    user_id: str = Field(min_length=2, max_length=80)


class Flashcard(BaseModel):
    id: str
    species: str
    media_type: str
    prompt: str
    answer: str
    media_url: str | None = None


class FlashcardCreate(BaseModel):
    species: str = Field(min_length=2, max_length=180)
    media_type: str = Field(min_length=2, max_length=40)
    prompt: str = Field(min_length=5)
    answer: str = Field(min_length=1)
    media_url: str | None = None


class UserPreferencesPayload(BaseModel):
    user_id: str = Field(min_length=2, max_length=80)
    observer_code: str | None = None
    display_name: str | None = None
    afdelinger: list[str] = Field(default_factory=list)
    categories: list[str] = Field(default_factory=lambda: ["SU", "SUB", "bemaerk"])
    exclude_species: list[str] = Field(default_factory=list)
    min_count_default: int = Field(default=1, ge=1)
    quiet_hours: str | None = None


class ObservationIngest(BaseModel):
    obsid: str = Field(min_length=1, max_length=32)
    observed_at: datetime
    species: str = Field(min_length=1, max_length=180)
    location: str = Field(min_length=1, max_length=180)
    category: str = Field(default="alm", max_length=24)
    count: int | None = None
    observer_code: str | None = None
    species_code: str | None = None
    species_latin: str | None = None
    location_id: str | None = None
    dof_afdeling: str | None = None
    note: str | None = None
    is_migration: bool = False
    is_matrikel: bool = False


class ObservationThreadSummary(BaseModel):
    thread_id: str
    day: str
    species: str
    location: str
    category: str
    latest_observed_at: datetime
    observations: int


class ObservationThreadDetail(BaseModel):
    thread_id: str
    day: str
    items: list[ObservationAlert]


class MatrixResponse(BaseModel):
    arter: list[str]
    koder: list[str]
    matrix: list[list[str]]
    totals: list[int]


class LegacyObservationUpdateRequest(BaseModel):
    rows: list[dict]


class DofSyncRequest(BaseModel):
    target_date: str | None = None


class BlacklistRequest(BaseModel):
    user_id: str = Field(min_length=2, max_length=80)
    reason: str | None = None


class AdminUserRequest(BaseModel):
    user_id: str = Field(min_length=2, max_length=80)
    role: str = Field(default="admin", min_length=3, max_length=24)


class PageViewRequest(BaseModel):
    path: str = Field(min_length=1, max_length=240)
    user_id: str | None = Field(default=None, max_length=80)
    referrer: str | None = None
    device_id: str | None = Field(default=None, max_length=120)
