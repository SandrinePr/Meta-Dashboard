"""Tests for flexible / fuzzy search matching."""

from __future__ import annotations

from pathlib import Path

from db.database import get_connection, initialize_database
from db.repository import (
    rebuild_search_index,
    sync_post_hashtags,
    upsert_account,
    upsert_post,
)
from search.engine import (
    FUZZY_RATIO_THRESHOLD,
    _build_fts_query,
    _like_seed,
    compact_normalize,
    result_matches_query,
    search,
)
from search.engine import SearchResult


def _setup(tmp_path: Path, monkeypatch) -> None:
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("DATABASE_PATH", str(db_path))
    initialize_database(db_path)


def _seed_flexible_posts(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        ig = upsert_account(
            conn,
            platform="instagram",
            external_id="ig-1",
            name="Luxe Klant",
            username="luxeklant",
        )
        fb = upsert_account(
            conn,
            platform="facebook",
            external_id="fb-1",
            name="Luxe Klant FB",
            username="luxeklantfb",
        )

        ig_hash = upsert_post(
            conn,
            platform="instagram",
            external_id="ig-pulsgroen",
            account_id=ig,
            content_type="post",
            text="Nieuw project live",
            permalink="https://instagram.com/p/pulsgroen",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-07-01T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, ig_hash.row_id, ["pulsgroen"])

        ig_hyphen = upsert_post(
            conn,
            platform="instagram",
            external_id="ig-puls-hyphen",
            account_id=ig,
            content_type="post",
            text="Tag: #puls-groen in de tuin",
            permalink="https://instagram.com/p/puls-hyphen",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-07-02T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, ig_hyphen.row_id, ["puls-groen"])

        fb_spaced = upsert_post(
            conn,
            platform="facebook",
            external_id="fb-puls-spaced",
            account_id=fb,
            content_type="post",
            text="Meer informatie: PULS Groenprojecten",
            permalink="https://facebook.com/puls-spaced",
            media_url=None,
            thumbnail_url=None,
            media_type="photo",
            published_at="2026-07-03T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, fb_spaced.row_id, [])

        fb_borek = upsert_post(
            conn,
            platform="facebook",
            external_id="fb-borek",
            account_id=fb,
            content_type="post",
            text="De tuinmeubelen van Borek - parasols and outdoor furniture",
            permalink="https://facebook.com/borek",
            media_url=None,
            thumbnail_url=None,
            media_type="photo",
            published_at="2026-07-04T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, fb_borek.row_id, [])

        ig_other = upsert_post(
            conn,
            platform="instagram",
            external_id="ig-other",
            account_id=ig,
            content_type="post",
            text="Geen relevante term hier",
            permalink="https://instagram.com/p/other",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-07-05T10:00:00+00:00",
            raw_json="{}",
        )
        sync_post_hashtags(conn, ig_other.row_id, ["garden"])

        rebuild_search_index(conn)
        conn.commit()


def test_compact_normalize_strips_separators_and_case() -> None:
    assert compact_normalize("PULS Groen") == "pulsgroen"
    assert compact_normalize("#puls-groen") == "pulsgroen"
    assert compact_normalize("puls_groen!") == "pulsgroen"
    assert compact_normalize("pulsgroen") == "pulsgroen"


def test_multiword_fts_requires_all_parts_and_compact_form() -> None:
    fts = _build_fts_query("Mint Interieur")
    assert " AND " in fts
    assert "mintinterieur" in fts.lower()
    assert _like_seed("Mint Interieur") == "mintinterieur"
    assert _like_seed("Mint Interieurs") == "mintinterieur"
    assert "interieur" in _build_fts_query("Mint Interieurs").lower()
    assert "groen*" in _build_fts_query("puls groen").lower()


def _result(
    text: str,
    hashtags: list[str] | None = None,
    account_name: str | None = None,
    account_username: str | None = None,
) -> SearchResult:
    return SearchResult(
        platform="facebook",
        entity_type="post",
        entity_id=1,
        published_at="2026-07-01T00:00:00+00:00",
        text=text,
        hashtags=hashtags or [],
        permalink=None,
        thumbnail_url=None,
        account_name=account_name,
        account_username=account_username,
    )


def test_result_matches_query_compact_and_fuzzy() -> None:
    result = _result("De tuinmeubelen van Borek")
    assert result_matches_query(result, "bore")
    assert result_matches_query(result, "BOREK")
    assert result_matches_query(result, "bork")  # fuzzy typo vs borek
    assert FUZZY_RATIO_THRESHOLD == 0.8


def test_multiword_query_matches_compound_variants_not_loose_words() -> None:
    """'Mint Interieur' is one name: keep spacing/plural/@/# variants, not loose words."""
    query = "Mint Interieur"
    assert result_matches_query(_result("Welkom bij Mint Interieur"), query)
    assert result_matches_query(_result("Welkom bij MintInterieur"), query)
    assert result_matches_query(_result("Welkom bij Mint Interieurs"), query)
    assert result_matches_query(_result("Welkom bij MintInterieurs"), query)
    assert result_matches_query(_result("Bekijk @mintinterieur even"), query)
    assert result_matches_query(_result("Nieuw project", ["mintinterieur"]), query)
    assert result_matches_query(_result("Zie #mintinterieur"), query)
    assert result_matches_query(_result("Mint Interieuren in Beek"), query)

    assert not result_matches_query(_result("Prachtig interieur in deze woning"), query)
    assert not result_matches_query(_result("Welkom bij Droominterieur"), query)
    assert not result_matches_query(
        _result("Mint groene muur bij Droominterieur"), query
    )
    assert not result_matches_query(
        _result("Mint plantjes en een mooi interieur"), query
    )

    for compact_query in (
        "MintInterieur",
        "MintInterieurs",
        "@mintinterieur",
        "#mintinterieur",
        "Mint Interieurs",
    ):
        assert result_matches_query(_result("Welkom bij Mint Interieur"), compact_query)
        assert result_matches_query(_result("Mint Interieuren in Beek"), compact_query)
        assert not result_matches_query(
            _result("Prachtig interieur in deze woning"), compact_query
        )
        assert not result_matches_query(_result("Welkom bij Droominterieur"), compact_query)
        assert not result_matches_query(
            _result("Mint plantjes en een mooi interieur"), compact_query
        )


def test_mint_interieur_search_excludes_droominterieur(
    tmp_path: Path, monkeypatch
) -> None:
    _setup(tmp_path, monkeypatch)
    with get_connection() as conn:
        account_id = upsert_account(conn, platform="instagram", external_id="ig-1")
        hits = [
            ("ig-phrase", "Welkom bij Mint Interieur", []),
            ("ig-concat", "Studio MintInterieurs", []),
            ("ig-tag", "Nieuw project", ["mintinterieur"]),
            ("ig-mention", "Bekijk @mintinterieur", []),
        ]
        misses = [
            ("ig-interieur-only", "Prachtig interieur in deze woning", []),
            ("ig-droom", "Welkom bij Droominterieur", []),
            ("ig-droom-mint", "Mint groene muur bij Droominterieur", []),
        ]
        for external_id, text, tags in hits + misses:
            post = upsert_post(
                conn,
                platform="instagram",
                external_id=external_id,
                account_id=account_id,
                content_type="post",
                text=text,
                permalink=f"https://instagram.com/p/{external_id}",
                media_url=None,
                thumbnail_url=None,
                media_type="IMAGE",
                published_at="2026-07-01T10:00:00+00:00",
                raw_json="{}",
            )
            sync_post_hashtags(conn, post.row_id, tags)
        rebuild_search_index(conn)
        conn.commit()

    for query in (
        "Mint Interieur",
        "MintInterieur",
        "Mint Interieurs",
        "MintInterieurs",
        "@mintinterieur",
        "#mintinterieur",
    ):
        texts = {item.text for item in search(query)}
        assert "Welkom bij Mint Interieur" in texts, query
        assert "Studio MintInterieurs" in texts, query
        assert "Nieuw project" in texts, query
        assert "Bekijk @mintinterieur" in texts, query
        assert "Prachtig interieur in deze woning" not in texts, query
        assert "Welkom bij Droominterieur" not in texts, query
        assert "Mint groene muur bij Droominterieur" not in texts, query


def test_puls_groen_variants_find_ig_and_fb(tmp_path: Path, monkeypatch) -> None:
    _seed_flexible_posts(tmp_path, monkeypatch)

    for query in ("puls groen", "pulsgroen", "PULS-GROEN", "#pulsgroen"):
        results = search(query, platforms={"instagram", "facebook"})
        texts_and_tags = {
            (item.platform, item.text, tuple(item.hashtags)) for item in results
        }
        assert any("pulsgroen" in item.hashtags for item in results), query
        assert any("PULS Groenprojecten" in item.text for item in results), query
        assert {item.platform for item in results} >= {"instagram", "facebook"}, query
        # Unrelated garden post must not appear solely due to loose FTS.
        assert not any(item.text == "Geen relevante term hier" for item in results), query
        assert texts_and_tags  # non-empty


def test_bore_finds_borek(tmp_path: Path, monkeypatch) -> None:
    _seed_flexible_posts(tmp_path, monkeypatch)
    results = search("bore")
    assert any("Borek" in item.text for item in results)
    results_full = search("borek")
    assert any("Borek" in item.text for item in results_full)


def test_search_dedupes_same_entity(tmp_path: Path, monkeypatch) -> None:
    _setup(tmp_path, monkeypatch)
    unique = "zzflexdedupe991"
    with get_connection() as conn:
        account_id = upsert_account(conn, platform="instagram", external_id="ig-1")
        post = upsert_post(
            conn,
            platform="instagram",
            external_id="post-1",
            account_id=account_id,
            content_type="post",
            text=f"{unique} special",
            permalink="https://instagram.com/p/1",
            media_url=None,
            thumbnail_url=None,
            media_type="IMAGE",
            published_at="2026-07-01T10:00:00+00:00",
            raw_json="{}",
        )
        post_id = post.row_id
        sync_post_hashtags(conn, post_id, [unique])
        # Index once via rebuild; matching via text + hashtag must still yield one row.
        rebuild_search_index(conn)
        conn.commit()

    results = search(unique)
    keys = [(item.entity_type, item.entity_id) for item in results]
    assert len(keys) == len(set(keys))
    assert sum(1 for item in results if item.entity_id == post_id) == 1
def test_search_includes_account_name(tmp_path: Path, monkeypatch) -> None:
    _seed_flexible_posts(tmp_path, monkeypatch)
    results = search("luxeklant")
    assert len(results) >= 1
    assert any(item.account_username == "luxeklant" for item in results)


def test_platform_filter_only_when_restricted(tmp_path: Path, monkeypatch) -> None:
    _seed_flexible_posts(tmp_path, monkeypatch)
    both = search("borek", platforms={"instagram", "facebook"})
    fb_only = search("borek", platforms={"facebook"})
    assert any(item.platform == "facebook" for item in both)
    assert all(item.platform == "facebook" for item in fb_only)
