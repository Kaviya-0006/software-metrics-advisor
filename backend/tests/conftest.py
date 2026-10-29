"""
Shared pytest fixtures.

Upload/API tests need an isolated database and upload directory so they
never touch the real dev `software_metrics.db` / `uploads/` used when
running the app manually. This overrides FastAPI's `get_db` dependency
with a throwaway SQLite database per test and points `settings.UPLOAD_DIR`
at a temp directory for the duration of each test.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import settings
from app.database.database import Base, get_db
from app.main import app


@pytest.fixture()
def client(tmp_path, monkeypatch):
    test_db_path = tmp_path / "test.db"
    test_engine = create_engine(
        f"sqlite:///{test_db_path}", connect_args={"check_same_thread": False}
    )
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)
    Base.metadata.create_all(bind=test_engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")

    with TestClient(app) as test_client:
        yield test_client

    app.dependency_overrides.clear()
