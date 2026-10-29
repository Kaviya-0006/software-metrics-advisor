"""
Phase 4 database-quality checks: foreign key relationship correctness,
cascade delete behavior, JSON field round-tripping, null-metric
persistence, and fresh-database initialization — using the real
SQLAlchemy models/engine, not mocks.
"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database.database import Base
from app.database.models import Module, Project


def _session_for(db_path):
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    return SessionLocal()


def test_fresh_database_initializes_all_tables(tmp_path):
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)

    table_names = set(Base.metadata.tables.keys())
    # Phase 8D added architecture_scans (one row per scan).
    assert table_names == {
        "projects", "modules", "architecture_scans",
        "project_identities", "project_versions",  # Phase 9A
        "file_fingerprints",  # Phase 9B
    }


def test_project_module_relationship_and_foreign_key(tmp_path):
    db = _session_for(tmp_path / "rel.db")
    try:
        project = Project(name="demo", status="analyzed")
        db.add(project)
        db.flush()

        module = Module(
            project_id=project.id, file_name="a.py", file_path="a.py", language="python",
        )
        db.add(module)
        db.commit()
        db.refresh(project)

        assert len(project.modules) == 1
        assert project.modules[0].file_name == "a.py"
        assert module.project.name == "demo"
    finally:
        db.close()


def test_deleting_project_cascades_to_modules(tmp_path):
    db = _session_for(tmp_path / "cascade.db")
    try:
        project = Project(name="demo", status="analyzed")
        db.add(project)
        db.flush()

        db.add(Module(project_id=project.id, file_name="a.py", file_path="a.py", language="python"))
        db.add(Module(project_id=project.id, file_name="b.py", file_path="b.py", language="python"))
        db.commit()

        project_id = project.id
        assert db.query(Module).filter(Module.project_id == project_id).count() == 2

        db.delete(project)
        db.commit()

        # Cascade must remove the orphaned Module rows, not leave them
        # pointing at a deleted project.
        assert db.query(Module).filter(Module.project_id == project_id).count() == 0
    finally:
        db.close()


def test_json_fields_round_trip(tmp_path):
    db_path = tmp_path / "json.db"
    db = _session_for(db_path)
    try:
        project = Project(
            name="demo", status="analyzed",
            category_scores={"maintainability": 70.0, "complexity": None},
        )
        db.add(project)
        db.flush()

        module = Module(
            project_id=project.id, file_name="a.py", file_path="a.py", language="python",
            risk_factors=["High cyclomatic complexity", "Low maintainability"],
            metrics_json={"loc": {"total": 10, "code": 8, "comments": 0, "blank": 2}},
        )
        db.add(module)
        db.commit()

        project_id, module_id = project.id, module.id
    finally:
        db.close()

    # Re-open a fresh session/connection against the same file to confirm
    # the JSON actually persisted to disk correctly, not just held in
    # Python memory on the original session.
    db2 = _session_for(db_path)
    try:
        reloaded_project = db2.query(Project).filter(Project.id == project_id).first()
        reloaded_module = db2.query(Module).filter(Module.id == module_id).first()

        assert reloaded_project.category_scores == {"maintainability": 70.0, "complexity": None}
        assert reloaded_module.risk_factors == ["High cyclomatic complexity", "Low maintainability"]
        assert reloaded_module.metrics_json["loc"]["total"] == 10
    finally:
        db2.close()


def test_null_metric_values_persist_as_null(tmp_path):
    db = _session_for(tmp_path / "nulls.db")
    try:
        project = Project(name="demo", status="analyzed")
        db.add(project)
        db.flush()

        module = Module(
            project_id=project.id, file_name="a.py", file_path="a.py", language="python",
            cohesion=None, depth_of_inheritance=None, maintainability_index=None,
            risk_score=None, risk_level=None,
        )
        db.add(module)
        db.commit()
        db.refresh(module)

        assert module.cohesion is None
        assert module.depth_of_inheritance is None
        assert module.maintainability_index is None
        assert module.risk_score is None
        assert module.risk_level is None
    finally:
        db.close()
