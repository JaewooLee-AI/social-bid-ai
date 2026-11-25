"""pytest 공통 격리 픽스처: 테스트마다 임시 SOCIALBID_DATA_DIR 사용."""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from app.config import seed_defaults
from app.db import init_db


@pytest.fixture()
def isolated_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    data_dir = tmp_path / "socialbid_data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("SOCIALBID_DATA_DIR", str(data_dir))
    seed_defaults()
    init_db()
    return data_dir
