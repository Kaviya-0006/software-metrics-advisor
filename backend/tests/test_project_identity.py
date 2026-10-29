"""Phase 9A - unit tests for project identity matching (pure functions)."""

from app.analyzer.identity import (
    AUTO_MATCH_MIN_STRUCTURE,
    auto_resolve,
    build_fingerprint,
    is_generic_name,
    name_keys_for,
    normalize_name,
    rank_candidates,
    strip_common_root,
    structure_similarity,
)


def _identity(identity_id, name, paths, languages=None, keys=None, version_count=1):
    fp = build_fingerprint(paths, languages or {"python": len(paths)})
    return {
        "identity_id": identity_id,
        "name": name,
        "name_keys": keys if keys is not None else name_keys_for(name, fp["root_folder"]),
        "version_count": version_count,
        "latest_version": version_count,
        "latest_scan_id": identity_id * 10,
        "fingerprint": fp,
    }


APP_PATHS = ["src/app.py", "src/models.py", "src/views.py", "tests/test_app.py"]


def test_normalize_name_strips_version_and_copy_markers():
    assert normalize_name("My-App_v2 (1).zip") == "my-app"
    assert normalize_name("proj-v2") == "proj"
    assert normalize_name("proj_final") == "proj"
    assert normalize_name("proj 2024-10-22") == "proj"
    assert normalize_name("myapp-main") == "myapp"
    assert normalize_name("app2") == "app2"  # digits glued to a name are part of the name
    assert normalize_name("") == ""


def test_generic_names_do_not_identify_projects():
    for name in ("download", "project-final-v3", "src", "backend", "x"):
        assert is_generic_name(normalize_name(name)), name
    assert not is_generic_name(normalize_name("inventory-service"))
    assert name_keys_for("download.zip", "src") == []


def test_root_folder_is_stripped_only_when_shared_by_every_path():
    assert strip_common_root(["proj/a.py", "proj/b/c.py"]) == (["a.py", "b/c.py"], "proj")
    assert strip_common_root(["proj/a.py", "other/b.py"])[1] is None
    assert strip_common_root(["a.py", "proj/b.py"])[1] is None


def test_root_folder_rename_does_not_change_structure_similarity():
    v1 = build_fingerprint([f"myapp-v1/{p}" for p in APP_PATHS])
    v2 = build_fingerprint([f"myapp-v2/{p}" for p in APP_PATHS])
    flat = build_fingerprint(APP_PATHS)
    assert structure_similarity(v1, v2) == 1.0
    assert structure_similarity(v1, flat) == 1.0  # zipped with vs. without a root folder


def test_name_and_structure_together_allow_automatic_match():
    existing = [_identity(1, "inventory", APP_PATHS)]
    upload = build_fingerprint(APP_PATHS + ["src/extra.py"])
    candidates = rank_candidates("inventory-v2.zip", upload, existing)
    assert len(candidates) == 1 and candidates[0].auto_match_eligible
    assert candidates[0].structure_score >= AUTO_MATCH_MIN_STRUCTURE
    assert auto_resolve(candidates).identity_id == 1


def test_same_name_with_unrelated_structure_is_never_merged():
    existing = [_identity(1, "inventory", APP_PATHS)]
    upload = build_fingerprint(["Main.java", "util/Helper.java"], {"java": 2})
    candidates = rank_candidates("inventory.zip", upload, existing)
    assert not any(c.auto_match_eligible for c in candidates)
    assert auto_resolve(candidates).identity_id is None


def test_similar_structure_with_a_different_name_is_only_a_suggestion():
    existing = [_identity(1, "inventory", APP_PATHS)]
    candidates = rank_candidates("billing.zip", build_fingerprint(APP_PATHS), existing)
    assert len(candidates) == 1
    assert candidates[0].auto_match_eligible is False
    assert any("never merged automatically" in r for r in candidates[0].reasons)
    assert auto_resolve(candidates).identity_id is None


def test_generic_zip_names_never_match_by_name():
    existing = [_identity(1, "download", APP_PATHS, keys=[])]
    candidates = rank_candidates("download.zip", build_fingerprint(APP_PATHS), existing)
    assert all(not c.name_match and not c.auto_match_eligible for c in candidates)


def test_zip_root_folder_name_is_a_second_name_signal():
    existing = [_identity(1, "myapp", APP_PATHS)]
    upload = build_fingerprint([f"myapp-main/{p}" for p in APP_PATHS])
    candidates = rank_candidates("download.zip", upload, existing)  # generic file name, real root folder
    assert candidates and candidates[0].auto_match_eligible


def test_two_equally_good_matches_are_ambiguous_not_guessed():
    existing = [_identity(1, "inventory", APP_PATHS), _identity(2, "inventory", APP_PATHS)]
    decision = auto_resolve(rank_candidates("inventory.zip", build_fingerprint(APP_PATHS), existing))
    assert decision.identity_id is None and decision.ambiguous


def test_no_candidates_means_new_project():
    decision = auto_resolve(rank_candidates("brand-new.zip", build_fingerprint(APP_PATHS), []))
    assert decision.identity_id is None and not decision.ambiguous
