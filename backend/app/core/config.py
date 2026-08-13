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

    dof_api_base_url: AnyHttpUrl = "https://api.dofbasen.dk/"
    dof_api_login_path: str = "/auth/login"
    dof_excel_base_url: AnyHttpUrl = "https://dofbasen.dk/excel/search_result1.php"

    admin_api_key: str = "dev-admin-key"
    superadmin_api_key: str = "dev-superadmin-key"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
