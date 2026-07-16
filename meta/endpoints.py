"""Meta Graph API endpoint and field definitions for this project."""

from __future__ import annotations

# Object/edge identifiers
ME_ACCOUNTS_EDGE = "me/accounts"
MEDIA_EDGE = "media"
COMMENTS_EDGE = "comments"
POSTS_EDGE = "posts"

# Default page size for Graph API list requests.
DEFAULT_LIMIT = 100

# Field sets used by phase 2+ data retrieval.
PAGES_FIELDS = (
    "id",
    "name",
    "access_token",
    "tasks",
    "instagram_business_account",
)

PAGE_INSTAGRAM_ACCOUNT_FIELDS = (
    "id",
    "name",
    "instagram_business_account{id,username}",
)

INSTAGRAM_MEDIA_FIELDS = (
    "id",
    "caption",
    "media_type",
    "media_product_type",
    "media_url",
    "thumbnail_url",
    "permalink",
    "timestamp",
    "like_count",
    "comments_count",
    "saved_count",
    "shares_count",
    "total_views_count",
    "view_count",
)

INSTAGRAM_COMMENT_FIELDS = (
    "id",
    "text",
    "timestamp",
    "username",
    "like_count",
)

FACEBOOK_POST_FIELDS = (
    "id",
    "message",
    "created_time",
    "permalink_url",
    "full_picture",
    "attachments{media,target,type,url,description,title,subattachments}",
    "likes.summary(true)",
    "reactions.summary(true)",
    "comments.summary(true)",
    "shares",
)

FACEBOOK_COMMENT_FIELDS = (
    "id",
    "message",
    "created_time",
    "from{id,name}",
    "attachment",
)


def as_fields_param(fields: tuple[str, ...]) -> str:
    """Convert a tuple of fields into Graph API query string format."""
    return ",".join(fields)
