"""Application settings, read from environment variables and the .env file."""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# backend/app/core/config.py -> parents[3] is the project root (traceveil/)
ROOT_DIR = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Traceveil"
    environment: str = "development"
    database_url: str = (
        "postgresql+psycopg://traceveil:traceveil_dev@localhost:5432/traceveil"
    )
    secret_key: str = "change-me"
    cors_origins: list[str] = ["http://localhost:5173"]
    data_dir: Path = ROOT_DIR / "backend" / ".data"
    access_token_minutes: int = 60

    @model_validator(mode="after")
    def _require_real_secret_outside_development(self) -> "Settings":
        weak = {"", "change-me", "change-me-to-a-long-random-string"}
        if self.environment != "development" and self.secret_key in weak:
            raise ValueError("Set a strong SECRET_KEY before running outside development.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()