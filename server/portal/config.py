from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

SEED_DIR = Path(__file__).resolve().parent / "seed"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_name: str = "DOF.tool"
    environment: str = "development"
    public_url: str = "http://localhost:8000"

    postgres_dsn: str = "postgresql+asyncpg://portal:portal@localhost:5432/portal"
    redis_dsn: str = "redis://localhost:6379/0"

    # Superadmin oprettes/opdateres ved opstart. Adgangskoden gemmes kun som argon2-hash.
    superadmin_obserkode: str = ""
    superadmin_password: str = ""

    session_days: int = 90
    cookie_secure: bool = False

    # DOFbasen: højst ét kald pr. dof_min_interval sekunder, uanset hvem der beder om det.
    dof_min_interval: float = 60.0
    dof_enabled: bool = True
    # Medie-scanner (dagens sjældne på observationer/index.php): sekunder mellem opslag, 0 = slået fra, ellers mindst 60
    dof_media_interval: float = 60.0
    dof_media_all_every: int = 10  # hvert N'te opslag henter alle fugle i stedet for kun de sjældne (1 = altid alle)
    dof_user_agent: str = "DOF.tool/1.0 (feltornitolog-portal; 1 kald/min)"
    dof_login_url: str = "https://krydslister.dofbasen.dk/api/v1/login"

    # Lokalt arkiv med månedsudtræk (dofbasen_ÅÅÅÅ_MM.csv)
    archive_dir: str = "/archive"

    media_dir: str = "/data/media"
    weather_dir: str = "/data/weather"  # midlertidige DMI-månedsfiler (≈0,5 GB, slettes efter parsing)

    vapid_subject: str = "mailto:admin@example.org"

    dmi_base: str = "https://opendataapi.dmi.dk"

    @field_validator("dof_min_interval")
    @classmethod
    def _at_least_one_minute(cls, v: float) -> float:
        # Hård grænse: DOFbasen må højst få ét kald i minuttet
        return max(60.0, v)

    @field_validator("dof_media_interval")
    @classmethod
    def _media_interval(cls, v: float) -> float:
        return 0.0 if v <= 0 else max(60.0, v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
