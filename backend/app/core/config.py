from functools import lru_cache

from pydantic import AnyHttpUrl, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DOF.tool"
    environment: str = "development"
    api_prefix: str = "/api/v1"

    postgres_dsn: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/dof_tool"
    )
    redis_dsn: str = "redis://localhost:6379/0"

    dof_login_url: AnyHttpUrl = "https://krydslister.dofbasen.dk/api/v1/login"
    dof_observer_profile_url: AnyHttpUrl = "https://dofbasen.dk/popobser.php"
    dof_excel_base_url: AnyHttpUrl = "https://dofbasen.dk/excel/search_result1.php"

    cors_allow_origins: list[str] = ["http://localhost:8000", "http://127.0.0.1:8000"]

    session_secret: str = "dev-session-secret-change-me"
    session_max_age_seconds: int = 60 * 60 * 24 * 30
    session_cookie_secure: bool = False
    login_max_attempts: int = 5
    login_attempt_window_seconds: int = 600

    admin_api_key: str = "dev-admin-key"
    superadmin_api_key: str = "dev-superadmin-key"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
