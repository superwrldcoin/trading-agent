import pytest

from tools import store


@pytest.fixture(autouse=True)
def isolated_store(tmp_path, monkeypatch):
    """Every test gets its own empty app database (and backup folder), so tests never touch memory/app.db
    and the house-rules switch starts at its default (on)."""
    monkeypatch.setattr(store, "DB_PATH", tmp_path / "app.db")
    monkeypatch.setattr(store, "LEGACY_DOCS", {"paper": tmp_path / "legacy-paper.json"})
    return tmp_path
