"""Robust Meta Graph API client for retrieval and pagination."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

import requests
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from config import get_settings
from meta.endpoints import (
    COMMENTS_EDGE,
    DEFAULT_LIMIT,
    INSTAGRAM_COMMENT_FIELDS,
    INSTAGRAM_MEDIA_FIELDS,
    ME_ACCOUNTS_EDGE,
    PAGE_INSTAGRAM_ACCOUNT_FIELDS,
    PAGES_FIELDS,
    POSTS_EDGE,
    FACEBOOK_POST_FIELDS,
    FACEBOOK_COMMENT_FIELDS,
    MEDIA_EDGE,
    as_fields_param,
)


logger = logging.getLogger(__name__)


class MetaClientError(Exception):
    """Base class for Meta API client errors."""


class MetaConfigError(MetaClientError):
    """Raised when required configuration values are missing."""


class MetaRequestError(MetaClientError):
    """Raised when a request to Graph API fails."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_code: int | None = None,
        error_subcode: int | None = None,
        is_transient: bool = False,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_code = error_code
        self.error_subcode = error_subcode
        self.is_transient = is_transient


TOKEN_EXPIRED_MESSAGE = (
    "Meta token verlopen. Genereer nieuwe User Token en run scripts/refresh_all_tokens.py."
)


def format_meta_client_error(exc: MetaClientError) -> str:
    """Return a user-facing sync error message for Meta client failures."""
    if isinstance(exc, MetaRequestError) and exc.error_code == 190:
        return TOKEN_EXPIRED_MESSAGE
    return str(exc)


def _is_retryable_error(exc: BaseException) -> bool:
    if isinstance(exc, (requests.Timeout, requests.ConnectionError)):
        return True
    if isinstance(exc, MetaRequestError):
        if exc.is_transient:
            return True
        if exc.status_code in {429, 500, 502, 503, 504}:
            return True
    return False


@dataclass(slots=True)
class MetaClient:
    """Graph API client with retries, paging, and typed helper methods."""

    base_url: str
    timeout_seconds: int
    user_access_token: str
    page_access_token: str
    default_page_id: str | None = None
    default_instagram_business_account_id: str | None = None
    session: requests.Session | None = None

    @classmethod
    def from_settings(cls) -> "MetaClient":
        """Build client from environment-backed settings."""
        settings = get_settings()
        return cls(
            base_url=f"https://graph.facebook.com/{settings.meta_graph_api_version}",
            timeout_seconds=settings.request_timeout_seconds,
            user_access_token=settings.meta_user_access_token,
            page_access_token=settings.meta_page_access_token,
            default_page_id=settings.meta_page_id or None,
            default_instagram_business_account_id=(
                settings.meta_instagram_business_account_id or None
            ),
        )

    @property
    def http(self) -> requests.Session:
        if self.session is None:
            self.session = requests.Session()
        return self.session

    def _validate_token(self, access_token: str | None, context: str) -> str:
        token = (access_token or "").strip()
        if not token:
            raise MetaConfigError(f"Missing access token for {context}.")
        return token

    @retry(
        retry=retry_if_exception(_is_retryable_error),
        wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
        stop=stop_after_attempt(5),
        reraise=True,
    )
    def _request_json(
        self,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Execute one GET request and return JSON with robust error mapping."""
        logger.debug("Meta request start url=%s params=%s", url, params)
        try:
            response = self.http.get(url, params=params, timeout=self.timeout_seconds)
        except (requests.Timeout, requests.ConnectionError) as exc:
            logger.warning("Meta network error url=%s error=%s", url, exc)
            raise

        if response.ok:
            try:
                payload: dict[str, Any] = response.json()
            except ValueError as exc:
                raise MetaRequestError(
                    "Meta API returned non-JSON response.",
                    status_code=response.status_code,
                ) from exc
            logger.debug("Meta request success url=%s", url)
            return payload

        error_payload: dict[str, Any] = {}
        try:
            parsed_error = response.json().get("error", {})
            if isinstance(parsed_error, dict):
                error_payload = parsed_error
        except ValueError:
            pass

        message = error_payload.get("message") or response.text or "Unknown Meta API error"
        meta_error = MetaRequestError(
            message,
            status_code=response.status_code,
            error_code=error_payload.get("code"),
            error_subcode=error_payload.get("error_subcode"),
            is_transient=bool(error_payload.get("is_transient", False)),
        )
        logger.warning(
            "Meta request failed status=%s code=%s transient=%s message=%s",
            meta_error.status_code,
            meta_error.error_code,
            meta_error.is_transient,
            message,
        )
        raise meta_error

    def get_json(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        access_token: str | None = None,
        use_page_token: bool = True,
    ) -> dict[str, Any]:
        """Perform a GET against `base_url/<endpoint>` and return JSON."""
        token_source = access_token
        if token_source is None:
            token_source = self.page_access_token if use_page_token else self.user_access_token
        token = self._validate_token(token_source, f"endpoint {endpoint}")

        merged_params = dict(params or {})
        merged_params["access_token"] = token
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        return self._request_json(url, params=merged_params)

    def get_paginated(
        self,
        endpoint: str,
        *,
        params: dict[str, Any] | None = None,
        access_token: str | None = None,
        use_page_token: bool = True,
        max_items: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch all pages using `paging.next` and return flattened `data`."""
        token_source = access_token
        if token_source is None:
            token_source = self.page_access_token if use_page_token else self.user_access_token
        token = self._validate_token(token_source, f"pagination endpoint {endpoint}")

        page_params = dict(params or {})
        page_params.setdefault("limit", DEFAULT_LIMIT)
        page_params["access_token"] = token

        next_url: str | None = f"{self.base_url}/{endpoint.lstrip('/')}"
        current_params: dict[str, Any] | None = page_params
        records: list[dict[str, Any]] = []

        while next_url:
            payload = self._request_json(next_url, params=current_params)
            data = payload.get("data", [])
            if not isinstance(data, list):
                raise MetaRequestError("Expected list payload in Graph API data field.")

            records.extend(data)
            logger.debug("Meta pagination page_fetched count=%s total=%s", len(data), len(records))

            if max_items is not None and len(records) >= max_items:
                return records[:max_items]

            paging = payload.get("paging", {})
            next_url = paging.get("next") if isinstance(paging, dict) else None
            # `paging.next` already includes all query params.
            current_params = None

        return records

    def get_pages(self) -> list[dict[str, Any]]:
        """Return Facebook Pages the authenticated user can access."""
        return self.get_paginated(
            ME_ACCOUNTS_EDGE,
            params={"fields": as_fields_param(PAGES_FIELDS)},
            use_page_token=False,
        )

    def get_instagram_business_account_id(self, page_id: str | None = None) -> str | None:
        """Resolve linked Instagram Business account ID for a Facebook Page."""
        resolved_page_id = (page_id or self.default_page_id or "").strip()
        if not resolved_page_id:
            raise MetaConfigError("Missing page_id for Instagram account discovery.")

        payload = self.get_json(
            resolved_page_id,
            params={"fields": as_fields_param(PAGE_INSTAGRAM_ACCOUNT_FIELDS)},
            use_page_token=True,
        )
        ig_account = payload.get("instagram_business_account")
        if isinstance(ig_account, dict):
            ig_id = ig_account.get("id")
            return str(ig_id) if ig_id else None
        return None

    def get_instagram_media(
        self,
        instagram_business_account_id: str | None = None,
        *,
        since: int | None = None,
        until: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch Instagram media for the provided IG business account."""
        ig_id = (
            instagram_business_account_id
            or self.default_instagram_business_account_id
            or ""
        ).strip()
        if not ig_id:
            raise MetaConfigError("Missing instagram_business_account_id for media fetch.")

        params: dict[str, Any] = {"fields": as_fields_param(INSTAGRAM_MEDIA_FIELDS)}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        if limit is not None:
            params["limit"] = limit

        return self.get_paginated(f"{ig_id}/{MEDIA_EDGE}", params=params, use_page_token=True)

    def get_instagram_comments(
        self,
        media_id: str,
        *,
        since: int | None = None,
        until: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch comments for one Instagram media object."""
        if not media_id.strip():
            raise MetaConfigError("media_id is required for Instagram comments fetch.")

        params: dict[str, Any] = {"fields": as_fields_param(INSTAGRAM_COMMENT_FIELDS)}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        if limit is not None:
            params["limit"] = limit

        return self.get_paginated(
            f"{media_id.strip()}/{COMMENTS_EDGE}",
            params=params,
            use_page_token=True,
        )

    def get_facebook_page_posts(
        self,
        page_id: str | None = None,
        *,
        since: int | None = None,
        until: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch Facebook posts created by the configured Page."""
        resolved_page_id = (page_id or self.default_page_id or "").strip()
        if not resolved_page_id:
            raise MetaConfigError("Missing page_id for Facebook posts fetch.")

        params: dict[str, Any] = {"fields": as_fields_param(FACEBOOK_POST_FIELDS)}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        if limit is not None:
            params["limit"] = limit

        return self.get_paginated(
            f"{resolved_page_id}/{POSTS_EDGE}",
            params=params,
            use_page_token=True,
        )

    def get_facebook_comments(
        self,
        post_id: str,
        *,
        since: int | None = None,
        until: int | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch comments for one Facebook post."""
        if not post_id.strip():
            raise MetaConfigError("post_id is required for Facebook comments fetch.")

        params: dict[str, Any] = {"fields": as_fields_param(FACEBOOK_COMMENT_FIELDS)}
        if since is not None:
            params["since"] = since
        if until is not None:
            params["until"] = until
        if limit is not None:
            params["limit"] = limit

        return self.get_paginated(
            f"{post_id.strip()}/{COMMENTS_EDGE}",
            params=params,
            use_page_token=True,
        )
