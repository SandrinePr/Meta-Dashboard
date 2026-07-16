"""Domain models for posts, comments, hashtags, and sync state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class Platform(str, Enum):
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"


class ContentType(str, Enum):
    POST = "post"
    COMMENT = "comment"


@dataclass(slots=True)
class Account:
    platform: Platform
    external_id: str
    name: Optional[str] = None
    username: Optional[str] = None
    page_id: Optional[str] = None


@dataclass(slots=True)
class Post:
    platform: Platform
    external_id: str
    account_external_id: str
    content_type: str
    text: str
    permalink: Optional[str]
    media_url: Optional[str]
    thumbnail_url: Optional[str]
    media_type: Optional[str]
    published_at: datetime
    hashtags: list[str] = field(default_factory=list)
    raw_json: Optional[str] = None


@dataclass(slots=True)
class Comment:
    platform: Platform
    external_id: str
    post_external_id: str
    text: str
    author_name: Optional[str]
    author_id: Optional[str]
    published_at: datetime
    raw_json: Optional[str] = None


@dataclass(slots=True)
class SyncState:
    account_external_id: str
    resource_type: str
    last_sync_at: Optional[datetime] = None
    last_cursor: Optional[str] = None
    last_success_at: Optional[datetime] = None
    last_error: Optional[str] = None
