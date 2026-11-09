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
    # DOF.tool-gruppen (observationer indtastet i DOF.tool): uden en registreret node i db-admin bruges en egen database
    # med dette navn på observationsgruppens primary – adskilt fra DOFbasens data (se dbgroups.DOFTOOL).
    doftool_dbname: str = "doftool"

    # Superadmin er denne obserkode (sættes ved opstart). Den logger ind med sin DOFbasen-adgangskode som alle andre.
    superadmin_obserkode: str = ""

    session_days: int = 90
    cookie_secure: bool = False  # login-cookien er altid Secure, når PUBLIC_URL er https

    # Privatlivspolitikken (/privatliv): den dataansvarlige og kontakt, og hvor længe sikkerhedskopier gemmes
    privacy_controller: str = ""
    privacy_email: str = ""
    privacy_backup_days: int = 30

    # DOFbasen: højst ét udtræk pr. dof_min_interval sekunder (mindst 120), uanset hvem der beder om det – live-hentning,
    # medie-scanner, historik og referencedata deler pladserne (dof/gateway.py)
    dof_min_interval: float = 120.0
    dof_enabled: bool = True
    # Medie-scanner (billeder, lyd og DKU-status fra observationer/index.php): et job i gatewayens udtræksbane, der følger
    # live-planen – højst én gang pr. live-interval og aldrig oftere end dette antal sekunder. 0 = slået fra.
    dof_media_interval: float = 600.0
    dof_media_all_every: int = 3  # hver N'te scanning henter alle fugle i stedet for kun de sjældne (1 = altid alle)
    dof_user_agent: str = "DOF.tool/1.0 (feltornitolog-portal; 1 kald/2 min)"
    dof_login_url: str = "https://krydslister.dofbasen.dk/api/v1/login"

    # Lokalt arkiv med månedsudtræk (dofbasen_ÅÅÅÅ_MM.csv)
    archive_dir: str = "/archive"
    # Månedsfiler uploadet via Admin → Drift (skrivbar; vinder over en fil med samme navn i archive_dir)
    archive_upload_dir: str = "/data/archive-upload"
    # Lokalitets-zip fra tools/dofbasen_lokaliteter (uploades via api, importeres af workeren – samme delte volume)
    location_import_dir: str = "/data/archive-upload/lokaliteter"

    media_dir: str = "/data/media"
    # Artikelmodulet: uploadede zip-filer med netfugl-arkivet (store; slettes efter vellykket import)
    article_import_dir: str = "/data/article-imports"
    photo_import_dir: str = "/data/photo-imports"  # fotoarkivet fra tools/netfugl_fotos: undermapper med netfugl-fotos/ eller Fotos-*.zip
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

    # Passkeys (services/passkeys.py): Zitadel gemmer brugernes passkeys og kontrollerer signaturerne. Den er kun intern –
    # browseren og appen taler alene med api'et. Tom ZITADEL_URL = passkeys slået fra (login kun med adgangskode).
    zitadel_url: str = ""  # fx http://zitadel:8080
    zitadel_pat_file: str = "/zitadel/pat.txt"  # servicekontoens token (skrives af Zitadel ved første start)
    # Navnet, authenticator-appen viser ved koden (services/totp.py). Fælles for DOF's apps, der bruger samme Zitadel.
    totp_issuer: str = "DOF"
    # Appens tilknytning til domænet (passkeys i appen): Androids signeringscertifikat (SHA-256, AA:BB:…) og Apples Team ID
    android_cert_sha256: str = ""
    ios_team_id: str = ""

    # E-mail (services/mail.py): bekræftelseskoder ved oprettelse af konto og skift af e-mail. Tom SMTP_HOST = intet sendes;
    # beskeden skrives i loggen i stedet (til udvikling). smtp_security: starttls (587), ssl (465) eller none.
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_security: str = "starttls"
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_from: str = ""  # fx «DOF.tool <noreply@doftool.dk>»; tom = smtp_user

    vapid_subject: str = "mailto:admin@example.org"
    # Firebase (push til Android-appen): servicekontoens JSON, direkte eller base64-kodet (se services/fcm.py)
    fcm_service_account_json: str = ""
    fcm_service_account_b64: str = ""

    dmi_base: str = "https://opendataapi.dmi.dk"

    @field_validator("dof_min_interval")
    @classmethod
    def _at_least_two_minutes(cls, v: float) -> float:
        # Hård grænse: DOFbasen må højst få ét udtræk hvert andet minut
        return max(120.0, v)

    @field_validator("dof_media_interval")
    @classmethod
    def _media_interval(cls, v: float) -> float:
        return 0.0 if v <= 0 else max(120.0, v)


@lru_cache
def get_settings() -> Settings:
    return Settings()
