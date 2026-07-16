"""Tests for sync mappers."""

from sync.mappers import extract_hashtags, normalize_instagram_post


def test_extract_hashtags_lowercase_and_unique() -> None:
    assert extract_hashtags("Hello #CULIMAAT #test #CULIMAAT") == ["culimaat", "test"]


def test_normalize_instagram_post_extracts_hashtags() -> None:
    payload = {
        "id": "1",
        "caption": "Check #Brand",
        "timestamp": "2026-07-01T10:00:00+0000",
        "permalink": "https://instagram.com/p/1",
        "media_type": "IMAGE",
    }
    result = normalize_instagram_post(payload)
    assert result["hashtags"] == ["brand"]
    assert result["external_id"] == "1"
