from datetime import datetime

from pydantic import BaseModel, Field


class HealthResponse(BaseModel):
    status: str
    postgres: str
    redis: str


class DofLoginRequest(BaseModel):
    username: str = Field(min_length=3)
    password: str = Field(min_length=3)


class DofConnectionRequest(DofLoginRequest):
    user_id: str = Field(min_length=2, max_length=80)


class DofLoginResponse(BaseModel):
    access_token: str
    refresh_token: str | None = None
    expires_in: int | None = None
    user: dict


class ObservationAlert(BaseModel):
    obsid: str
    species: str
    category: str
    location: str
    count: int | None = None
    observed_at: datetime
    enriched_class: str = "ALM"
    remarkable_count: int | None = None


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
    enriched_class: str = Field(default="ALM", max_length=32)
    remarkable_count: int | None = None
    count: int | None = None
    observer_code: str | None = None
    species_code: str | None = None
    species_latin: str | None = None
    location_id: str | None = None
    latitude: float | None = None
    longitude: float | None = None
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


class CommunityRegisterRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)


class CommunityLoginRequest(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class CommunityUserRead(BaseModel):
    id: str
    email: str
    display_name: str
    role: str
    avatar_url: str | None = None


class CommunityEventCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    description: str = Field(min_length=3)
    starts_at: datetime
    ends_at: datetime | None = None
    location: str = Field(min_length=2, max_length=180)
    category: str = Field(default="fællesskab", min_length=2, max_length=60)
    image_url: str | None = None
    signup_url: str | None = None
    max_participants: int | None = Field(default=None, ge=1, le=10000)
    recurrence_rule: str | None = Field(default=None, pattern="^(weekly|monthly)$")
    recurrence_until: datetime | None = None


class EventCommentCreate(BaseModel):
    body: str = Field(min_length=1, max_length=2000)


class CommunityNewsCreate(BaseModel):
    title: str = Field(min_length=3, max_length=180)
    body_markdown: str = Field(min_length=3)
    category: str = Field(default="nyt", min_length=2, max_length=60)
    image_url: str | None = None


class CommunityContactCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: str = Field(min_length=2, max_length=120)
    email: str | None = None
    phone: str | None = None
    image_url: str | None = None
    sort_order: int = Field(default=0, ge=0)


class CommunityDocumentCreate(BaseModel):
    title: str = Field(min_length=2, max_length=180)
    description: str | None = None
    category: str = Field(default="dokument", min_length=2, max_length=60)
    file_url: str = Field(min_length=5)


class PushSubscriptionCreate(BaseModel):
    endpoint: str = Field(min_length=10)
    keys: dict[str, str]


class CommunityAdminCreate(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    display_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=8, max_length=128)
