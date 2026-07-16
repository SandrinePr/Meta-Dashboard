"""Tests for Meta setup helpers."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from meta_setup import get_missing_keys, mask_secret, update_env_file


def test_mask_secret_hides_middle() -> None:
    assert mask_secret("ABCDEFGHIJKLMNOP") == "ABCD...MNOP"


def test_mask_secret_short_value() -> None:
    assert mask_secret("abc") == "***"


def test_get_missing_keys_detects_placeholders() -> None:
    values = {
        "META_APP_ID": "your_meta_app_id",
        "META_APP_SECRET": "secret",
        "META_USER_ACCESS_TOKEN": "",
    }
    missing = get_missing_keys(values, ("META_APP_ID", "META_APP_SECRET", "META_USER_ACCESS_TOKEN"))
    assert "META_APP_ID" in missing
    assert "META_USER_ACCESS_TOKEN" in missing
    assert "META_APP_SECRET" not in missing


def test_update_env_file_replaces_existing_key(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "META_APP_ID=old\nMETA_USER_ACCESS_TOKEN=oldtoken\n",
        encoding="utf-8",
    )

    original_env = Path(__file__).resolve().parents[1] / ".env"
    # Patch ENV_PATH by writing through helper's expected file manually.
    import meta_setup

    original_path = meta_setup.ENV_PATH
    meta_setup.ENV_PATH = env_file
    try:
        update_env_file({"META_USER_ACCESS_TOKEN": "newtoken"})
    finally:
        meta_setup.ENV_PATH = original_path

    content = env_file.read_text(encoding="utf-8")
    assert "META_USER_ACCESS_TOKEN=newtoken" in content
    assert "oldtoken" not in content
