"""Tests for image URL resolution."""

import json

from ui.media import classify_content_type, extract_post_stats, resolve_display_image_url


def test_extract_instagram_stats() -> None:
    raw = json.dumps({"like_count": 12, "comments_count": 4, "play_count": 230})
    stats = extract_post_stats("instagram", raw)
    assert stats == {"likes": 12, "comments": 4, "views": 230}


def test_extract_instagram_stats_skips_missing() -> None:
    raw = json.dumps({"like_count": 5})
    assert extract_post_stats("instagram", raw) == {"likes": 5}


def test_extract_facebook_stats_from_summaries() -> None:
    raw = json.dumps(
        {
            "likes": {"summary": {"total_count": 20}},
            "comments": {"summary": {"total_count": 7}},
            "shares": {"count": 3},
        }
    )
    stats = extract_post_stats("facebook", raw)
    assert stats == {"likes": 20, "comments": 7, "shares": 3}


def test_extract_stats_handles_bad_json() -> None:
    assert extract_post_stats("instagram", "not json") == {}
    assert extract_post_stats("facebook", None) == {}


def test_extract_comment_stats_likes_only() -> None:
    from ui.media import extract_comment_stats

    assert extract_comment_stats(json.dumps({"like_count": 8, "text": "hi"})) == {"likes": 8}
    assert extract_comment_stats(json.dumps({"text": "hi"})) == {}
    assert extract_comment_stats(None) == {}


def test_classify_instagram_video_is_reel() -> None:
    assert classify_content_type("instagram", media_type="VIDEO") == "Reel"


def test_classify_instagram_reels_product_type_is_reel() -> None:
    raw = json.dumps({"media_type": "VIDEO", "media_product_type": "REELS"})
    assert classify_content_type("instagram", media_type="VIDEO", raw_json=raw) == "Reel"


def test_classify_instagram_carousel() -> None:
    assert classify_content_type("instagram", media_type="CAROUSEL_ALBUM") == "Carousel"


def test_classify_instagram_image_is_post() -> None:
    assert classify_content_type("instagram", media_type="IMAGE") == "Post"


def test_classify_facebook_defaults_to_post() -> None:
    assert classify_content_type("facebook", media_type="video") == "Post"


def test_classify_facebook_multiple_subattachments_is_carousel() -> None:
    raw = json.dumps(
        {
            "attachments": {
                "data": [
                    {
                        "subattachments": {
                            "data": [{"type": "photo"}, {"type": "photo"}, {"type": "photo"}]
                        }
                    }
                ]
            }
        }
    )
    assert classify_content_type("facebook", raw_json=raw) == "Carousel"


def test_classify_facebook_multiple_attachments_is_carousel() -> None:
    raw = json.dumps({"attachments": {"data": [{"type": "photo"}, {"type": "photo"}]}})
    assert classify_content_type("facebook", raw_json=raw) == "Carousel"


def test_classify_facebook_single_attachment_is_post() -> None:
    raw = json.dumps({"attachments": {"data": [{"type": "photo"}]}})
    assert classify_content_type("facebook", raw_json=raw) == "Post"


def test_classify_facebook_child_attachments_is_carousel() -> None:
    raw = json.dumps({"child_attachments": [{"id": "1"}, {"id": "2"}]})
    assert classify_content_type("facebook", raw_json=raw) == "Carousel"


def test_classify_unknown_defaults_to_post() -> None:
    assert classify_content_type("instagram") == "Post"


def test_video_prefers_thumbnail_before_media_url() -> None:
    url, source = resolve_display_image_url(
        thumbnail_url="https://example.com/thumb.jpg",
        media_url="https://example.com/video.mp4",
        media_type="VIDEO",
    )
    assert url == "https://example.com/thumb.jpg"
    assert source == "thumbnail_url"


def test_image_prefers_media_url_before_thumbnail() -> None:
    url, source = resolve_display_image_url(
        thumbnail_url="https://example.com/thumb.jpg",
        media_url="https://example.com/image.jpg",
        media_type="IMAGE",
    )
    assert url == "https://example.com/image.jpg"
    assert source == "media_url"


def test_raw_json_full_picture_fallback() -> None:
    url, source = resolve_display_image_url(
        raw_json='{"full_picture":"https://example.com/fb.jpg"}',
    )
    assert url == "https://example.com/fb.jpg"
    assert source == "raw_json.full_picture"


def test_search_index_thumbnail_used_when_db_empty() -> None:
    url, source = resolve_display_image_url(
        search_index_thumbnail="https://example.com/index.jpg",
    )
    assert url == "https://example.com/index.jpg"
    assert source == "search_index.thumbnail_url"
