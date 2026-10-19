"""Tests for local thumbnail compression."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from sync.thumbnails import save_compressed_jpeg


def test_save_compressed_jpeg_writes_file(tmp_path: Path) -> None:
    image = Image.new("RGB", (800, 600), color=(30, 80, 120))
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG")
    target = tmp_path / "post_1.jpg"
    assert save_compressed_jpeg(buffer.getvalue(), target) is True
    assert target.exists()
    assert target.stat().st_size > 0
    saved = Image.open(target)
    assert saved.size[0] <= 480
    assert saved.size[1] <= 480
