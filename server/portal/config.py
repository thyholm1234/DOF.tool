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
    # Databaseregistret (db-admin-containeren). Tomt = ingen opdeling: POSTGRES_DSN bruges til både brugere og observationer.
    dbadmin_url: str = ""
    dbadmin_service_token: str = ""

    # Superadmin er denne obserkode (sættes ved opstart). Den logger ind med sin DOFbasen-adgangskode som alle andre.
    superadmin_obserkode: str = ""

    session_days: int = 90
    cookie_secure: bool = False  # login-cookien er altid Secure, når PUBLIC_URL er https

    # Privatlivspolitikken (/privatliv): den dataansvarlige og kontakt, og hvor længe sikkerhedskopier gemmes
    privacy_controller: str = ""
    privacy_email: str = ""
    privacy_backup_days: int = 30

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
    # Månedsfiler uploadet via Admin → Drift (skrivbar; vinder over en fil med samme navn i archive_dir)
    archive_upload_dir: str = "/data/archive-upload"

    media_dir: str = "/data/media"
    # Objektlager (S3, RustFS i compose) til filer i observationer. Tom S3_ENDPOINT = filerne ligger under media_dir/obs.
    s3_endpoint: str = ""  # fx rustfs:9000
    s3_access_key: str = ""
    s3_secret_key: str = ""
    s3_bucket: str = "doftool-media"
    s3_secure: bool = False
    s3_region: str = ""
    # Talemodellen til appens tale-indtastning (bygges af tools/speech-model/build.sh); hentes af telefonen fra /models/
    models_dir: str = "/data/models"
    weather_dir: str = "/data/weather"  # midlertidige DMI-månedsfiler (≈0,5 GB, slettes efter parsing)

    vapid_subject: str = "mailto:admin@example.org"
    # Firebase (push til Android-appen): servicekontoens JSON, direkte eller base64-kodet (se services/fcm.py)
    fcm_service_account_json: str = ""
    fcm_service_account_b64: str = ""

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
