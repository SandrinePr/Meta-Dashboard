"""Application configuration loader for phase 0/1."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    """Typed runtime settings loaded from environment variables."""

    app_env: str
    app_debug: bool
    database_path: Path
    meta_graph_api_version: str
    meta_app_id: str
    meta_app_secret: str
    meta_user_access_token: str
    meta_page_id: str
    meta_page_access_token: str
    meta_instagram_business_account_id: str
    sync_page_size: int
    request_timeout_seconds: int


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_settings() -> Settings:
    """Load and validate app settings from .env/environment."""
    load_dotenv()

    database_path = Path(os.getenv("DATABASE_PATH", "data/social_search.db"))

    return Settings(
        app_env=os.getenv("APP_ENV", "development"),
        app_debug=_to_bool(os.getenv("APP_DEBUG", "true"), default=True),
        database_path=database_path,
        meta_graph_api_version=os.getenv("META_GRAPH_API_VERSION", "v25.0"),
        meta_app_id=os.getenv("META_APP_ID", ""),
        meta_app_secret=os.getenv("META_APP_SECRET", ""),
        meta_user_access_token=os.getenv("META_USER_ACCESS_TOKEN", ""),
        meta_page_id=os.getenv("META_PAGE_ID", ""),
        meta_page_access_token=os.getenv("META_PAGE_ACCESS_TOKEN", ""),
        meta_instagram_business_account_id=os.getenv("META_INSTAGRAM_BUSINESS_ACCOUNT_ID", ""),
        sync_page_size=int(os.getenv("SYNC_PAGE_SIZE", "100")),
        request_timeout_seconds=int(os.getenv("REQUEST_TIMEOUT_SECONDS", "30")),
    )
