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
    # Files and jobs
    retention_days: int = 7
    max_upload_bytes: int = 10_000_000
    worker_poll_seconds: float = 2.0
    preserve_match_images: bool = True  # keep a private copy of each matched image as evidence

    # Video
    max_video_bytes: int = 100_000_000
    max_video_seconds: int = 600
    video_verify_enabled: bool = True
    video_verify_max_bytes: int = 6_000_000
    video_verify_max_candidates: int = 6
    scan_dev_targets: list[str] = []  # development only, e.g. ["http://localhost:8001"]
    # Crawler
    crawler_contact: str = "contact-not-configured"
    crawler_min_delay_seconds: float = 0.5
    crawler_timeout_seconds: float = 12.0
    crawler_concurrency: int = 12
    max_html_bytes: int = 3_000_000
    max_image_bytes: int = 3_000_000
    netguard_enabled: bool = True
    netguard_allow_loopback: bool = True  # development only
    # Scan limits
    scan_max_pages_per_domain: int = 5
    scan_max_images_per_page: int = 40
    scan_max_total_images: int = 8000
    scan_max_seconds: int = 600
    scan_parallel_domains: int = 6
    scan_cpu_workers: int = 4

    @model_validator(mode="after")
    def _require_real_secret_outside_development(self) -> "Settings":
        weak = {"", "change-me", "change-me-to-a-long-random-string"}
        if self.environment != "development" and self.secret_key in weak:
            raise ValueError("Set a strong SECRET_KEY before running outside development.")
        if self.environment != "development" and (
            self.netguard_allow_loopback or not self.netguard_enabled
        ):
            raise ValueError("Network guard relaxations are for development only.")
        if self.environment != "development" and self.scan_dev_targets:
            raise ValueError("SCAN_DEV_TARGETS is for development only.")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()