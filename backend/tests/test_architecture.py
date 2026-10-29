"""
Phase 8A — Project Architecture Intelligence (foundation).

Covers layer classification (frontend / backend / api / database / shared /
tests / configuration / unknown), the evidence-based confidence model, the
aggregate `ArchitectureSummary`, and — through the real scanner and analysis
pipeline — that the feature reuses the existing language detection and leaves
the existing analysis output untouched.
"""

import random
from pathlib import Path

import pytest

from app.analyzer import architecture as arch
from app.analyzer.architecture import (
    CONFIDENCE_HIGH,
    CONFIDENCE_LOW,
    CONFIDENCE_MEDIUM,
    analyze_architecture,
    classify_file,
)
from app.analyzer.engine import analyze_project
from app.analyzer.scanner import LANGUAGE_BY_EXT, discover_source_files


def entry(path: str, language: str, loc: int = 10) -> dict:
    """A file record in the stored `ModuleResult.to_dict()` shape."""
    return {"file_path": path, "language": language, "loc": {"total": loc}}


def layer_of(path: str, language: str) -> tuple[str, str]:
    result = classify_file(path, language)
    return result.layer, result.confidence


# ------------------------------------------------------- frontend detection


@pytest.mark.parametrize("path", ["frontend/App.tsx", "frontend/src/api.ts", "client/index.js", "front-end/main.jsx"])
def test_strong_frontend_directories_with_web_languages_are_high(path):
    language = "typescript" if path.endswith(("ts", "tsx")) else "javascript"
    assert layer_of(path, language) == ("frontend", CONFIDENCE_HIGH)


def test_frontend_directory_with_a_non_web_language_is_only_medium():
    # Directory says frontend, language does not support it: partial evidence.
    assert layer_of("frontend/tools/build.py", "python") == ("frontend", CONFIDENCE_MEDIUM)


@pytest.mark.parametrize("path", ["web/App.tsx", "ui/Button.js", "app/page.tsx"])
def test_weak_frontend_directories_need_a_web_language_and_are_at_most_medium(path):
    language = "typescript" if path.endswith("tsx") else "javascript"
    assert layer_of(path, language) == ("frontend", CONFIDENCE_MEDIUM)


@pytest.mark.parametrize("path", ["app/main.py", "ui/helpers.py", "web/Server.java", "app/Main.cpp"])
def test_weak_frontend_directory_with_a_server_language_is_not_claimed(path):
    language = {"py": "python", "java": "java", "cpp": "cpp"}[path.rsplit(".", 1)[1]]
    assert layer_of(path, language)[0] == "unknown"


def test_jsx_and_tsx_files_alone_are_low_confidence_frontend():
    assert layer_of("src/components/Button.tsx", "typescript") == ("frontend", CONFIDENCE_LOW)
    assert layer_of("src/legacy/Widget.jsx", "javascript") == ("frontend", CONFIDENCE_LOW)


# -------------------------------------------------------- backend detection


@pytest.mark.parametrize(
    "path,language",
    [
        ("backend/AuthService.java", "java"),
        ("backend/app/main.py", "python"),
        ("server/app.py", "python"),
        ("back-end/core.cpp", "cpp"),
        ("server/index.js", "javascript"),  # Node servers are common
    ],
)
def test_strong_backend_directories_are_high_for_any_language(path, language):
    assert layer_of(path, language) == ("backend", CONFIDENCE_HIGH)


def test_services_directory_is_weak_backend_evidence():
    assert layer_of("services/Payments.java", "java") == ("backend", CONFIDENCE_MEDIUM)
    # `services/` is just as common in client-side code, so JS/TS stays LOW.
    assert layer_of("services/payments.ts", "typescript") == ("backend", CONFIDENCE_LOW)


def test_api_directory_is_its_own_layer_with_language_dependent_confidence():
    assert layer_of("api/handler.py", "python") == ("api", CONFIDENCE_MEDIUM)
    assert layer_of("api/handler.ts", "typescript") == ("api", CONFIDENCE_LOW)


def test_api_directory_inside_a_side_directory_does_not_override_it():
    # An API *client* inside the frontend is frontend code, not the API layer.
    assert layer_of("frontend/src/api/client.ts", "typescript") == ("frontend", CONFIDENCE_HIGH)
    assert layer_of("backend/api/routes.py", "python") == ("backend", CONFIDENCE_HIGH)


def test_controller_file_name_alone_is_low_confidence_api():
    assert layer_of("src/PaymentController.java", "java") == ("api", CONFIDENCE_LOW)


# --------------------------------------------------- test directory detection


@pytest.mark.parametrize(
    "path,language",
    [
        ("tests/test_auth.py", "python"),
        ("test/AuthSpec.java", "java"),
        ("backend/src/test/java/AuthTest.java", "java"),
        ("frontend/__tests__/App.tsx", "typescript"),
        ("e2e/login.ts", "typescript"),
    ],
)
def test_strong_test_directories_win_over_side_directories(path, language):
    assert layer_of(path, language) == ("tests", CONFIDENCE_HIGH)


def test_test_file_name_without_any_directory_evidence_is_low():
    assert layer_of("src/App.test.tsx", "typescript") == ("tests", CONFIDENCE_LOW)
    assert layer_of("src/test_utils.py", "python") == ("tests", CONFIDENCE_LOW)
    assert layer_of("src/AuthServiceTest.java", "java") == ("tests", CONFIDENCE_LOW)


def test_test_file_name_with_a_conflicting_directory_is_medium():
    result = classify_file("frontend/App.spec.ts", "typescript")
    assert (result.layer, result.confidence) == ("tests", CONFIDENCE_MEDIUM)
    assert any("different layer" in item for item in result.evidence)


@pytest.mark.parametrize("path,language", [("src/Latest.java", "java"), ("src/Contest.py", "python"), ("src/Test.java", "java")])
def test_names_that_merely_contain_test_are_not_tests(path, language):
    assert layer_of(path, language)[0] == "unknown"


# ----------------------------------------------- other layers and precedence


def test_database_shared_and_configuration_layers():
    assert layer_of("db/connection.py", "python") == ("database", CONFIDENCE_HIGH)
    assert layer_of("migrations/001_init.py", "python") == ("database", CONFIDENCE_HIGH)
    assert layer_of("shared/types.ts", "typescript") == ("shared", CONFIDENCE_HIGH)
    assert layer_of("common/format.py", "python") == ("shared", CONFIDENCE_HIGH)
    assert layer_of("config/settings.py", "python") == ("configuration", CONFIDENCE_HIGH)
    assert layer_of("lib/helpers.js", "javascript") == ("shared", CONFIDENCE_LOW)
    assert layer_of("repositories/UserRepository.java", "java") == ("database", CONFIDENCE_LOW)


def test_configuration_file_names_are_low_confidence():
    for path, language in [("vite.config.ts", "typescript"), ("settings.py", "python"), (".eslintrc.js", "javascript")]:
        assert layer_of(path, language) == ("configuration", CONFIDENCE_LOW)


def test_innermost_strong_directory_decides_between_roles():
    assert layer_of("backend/config/db.py", "python")[0] == "configuration"
    assert layer_of("frontend/src/shared/format.ts", "typescript")[0] == "shared"


def test_strong_evidence_beats_weak_evidence():
    # `utils` is a weak shared hint, `frontend` a strong one.
    assert layer_of("frontend/src/utils/format.ts", "typescript") == ("frontend", CONFIDENCE_HIGH)


# ------------------------------------------------- nested directory structures


def test_nested_frontend_and_backend_directories():
    assert layer_of("monorepo/packages/frontend/src/App.tsx", "typescript") == ("frontend", CONFIDENCE_HIGH)
    assert layer_of("monorepo/packages/backend/src/Main.java", "java") == ("backend", CONFIDENCE_HIGH)
    # strong `backend` beats weak `services` further out
    assert layer_of("platform/services/backend/Orders.java", "java") == ("backend", CONFIDENCE_HIGH)
    # strong `frontend` beats weak `web`
    assert layer_of("platform/web/frontend/Home.tsx", "typescript") == ("frontend", CONFIDENCE_HIGH)


def test_contradictory_frontend_and_backend_directories_cap_confidence():
    result = classify_file("frontend/backend/handler.ts", "typescript")
    assert (result.layer, result.confidence) == ("backend", CONFIDENCE_MEDIUM)  # innermost wins
    assert any("both a frontend and a backend" in item for item in result.evidence)


def test_directory_matching_is_whole_segment_and_case_insensitive():
    assert layer_of("Frontend/App.tsx", "typescript") == ("frontend", CONFIDENCE_HIGH)
    assert layer_of("SERVER\\app.py", "python") == ("backend", CONFIDENCE_HIGH)  # Windows separators
    for path in ["frontend-tools/build.js", "myserver/app.py", "backend_old/x.py", "clientele/x.js"]:
        assert layer_of(path, "javascript" if path.endswith("js") else "python")[0] == "unknown"


# --------------------------------------------- unknown / unconventional layouts


def test_unknown_directory_is_unclassified_not_guessed():
    result = classify_file("misc/stuff.py", "python")
    assert result.layer == "unknown"
    assert result.confidence == CONFIDENCE_LOW
    assert result.evidence == ("no conventional directory or file-name evidence",)


def test_project_without_conventional_folder_names_has_no_detected_layers():
    summary = analyze_architecture(
        [
            entry("src/main.py", "python", 40),
            entry("src/models.py", "python", 60),
            entry("src/Main.java", "java", 25),
            entry("scripts/run.js", "javascript", 8),
        ]
    )
    assert summary.detected_layers == []
    assert summary.files_by_layer == {"unknown": 4}
    assert summary.total_files == 4 and summary.total_loc == 133
    assert summary.confidence == CONFIDENCE_LOW
    assert not summary.is_multi_layer


# --------------------------------------------------- summary: mixed / single


def test_mixed_language_project_matches_the_documented_example():
    files = [entry(f"frontend/src/c{i}.tsx", "typescript", 100) for i in range(3)]
    files += [entry("frontend/src/legacy.js", "javascript", 50)]
    files += [entry(f"backend/src/S{i}.java", "java", 200) for i in range(2)]
    summary = analyze_architecture(files)

    assert summary.detected_layers == ["frontend", "backend"]
    assert summary.is_multi_layer
    assert summary.to_dict() == {
        "layers": {
            "frontend": {"languages": {"typescript": 3, "javascript": 1}, "files": 4, "loc": 350, "confidence": "high"},
            "backend": {"languages": {"java": 2}, "files": 2, "loc": 400, "confidence": "high"},
        },
        "detected_layers": ["frontend", "backend"],
        "total_files": 6,
        "total_loc": 750,
        "languages": {"typescript": 3, "java": 2, "javascript": 1},
        "confidence": "high",
    }
    assert summary.languages_by_layer == {"frontend": {"typescript": 3, "javascript": 1}, "backend": {"java": 2}}
    assert summary.files_by_layer == {"frontend": 4, "backend": 2}


def test_single_language_project_with_structure():
    summary = analyze_architecture(
        [
            entry("src/app.py", "python", 100),
            entry("src/util.py", "python", 50),
            entry("tests/test_app.py", "python", 30),
        ]
    )
    assert summary.languages == {"python": 3}
    assert summary.detected_layers == ["tests"]
    assert summary.files_by_layer == {"tests": 1, "unknown": 2}
    assert summary.layers["unknown"].languages == {"python": 2}
    assert not summary.is_multi_layer


def test_single_language_project_split_into_layers():
    summary = analyze_architecture([entry("backend/a.py", "python"), entry("tests/test_a.py", "python"), entry("config/c.py", "python")])
    assert summary.languages == {"python": 3}
    assert summary.detected_layers == ["backend", "tests", "configuration"]
    assert summary.is_multi_layer


def test_layers_are_reported_in_a_stable_order_regardless_of_input_order():
    files = [
        entry("tests/test_a.py", "python"),
        entry("backend/a.py", "python"),
        entry("frontend/a.ts", "typescript"),
        entry("shared/t.ts", "typescript"),
        entry("db/m.py", "python"),
        entry("api/h.py", "python"),
        entry("config/c.py", "python"),
        entry("other/x.py", "python"),
    ]
    expected = ["frontend", "backend", "api", "database", "shared", "tests", "configuration"]
    assert analyze_architecture(files).detected_layers == expected
    shuffled = files[:]
    random.Random(7).shuffle(shuffled)
    assert analyze_architecture(shuffled).to_dict() == analyze_architecture(files).to_dict()


def test_files_by_layer_always_sums_to_total_files_and_loc_reconciles():
    files = [entry("frontend/a.tsx", "typescript", 12), entry("backend/b.java", "java", 30), entry("x/y.py", "python", 5)]
    summary = analyze_architecture(files)
    assert sum(summary.files_by_layer.values()) == summary.total_files == 3
    assert sum(layer.loc for layer in summary.layers.values()) == summary.total_loc == 47


def test_empty_project():
    summary = analyze_architecture([])
    assert (summary.total_files, summary.total_loc, summary.confidence) == (0, 0, CONFIDENCE_LOW)
    assert summary.detected_layers == [] and summary.layers == {}


# ---------------------------------------------------- confidence calculation


def test_layer_confidence_is_the_mean_of_file_confidences():
    all_high = analyze_architecture([entry("frontend/a.tsx", "typescript"), entry("frontend/b.ts", "typescript")])
    assert all_high.layers["frontend"].confidence == CONFIDENCE_HIGH

    # HIGH (3) + MEDIUM (2) -> mean 2.5 -> below the 2.6 HIGH threshold
    mixed = analyze_architecture([entry("frontend/a.tsx", "typescript"), entry("frontend/tool.py", "python")])
    assert mixed.layers["frontend"].confidence == CONFIDENCE_MEDIUM

    # three HIGH + one MEDIUM -> mean 2.75 -> HIGH
    mostly_high = analyze_architecture([entry(f"frontend/f{i}.ts", "typescript") for i in range(3)] + [entry("frontend/t.py", "python")])
    assert mostly_high.layers["frontend"].confidence == CONFIDENCE_HIGH

    # name-only evidence is LOW per file, so the layer is LOW
    low = analyze_architecture([entry("src/a.test.ts", "typescript"), entry("src/b.test.ts", "typescript")])
    assert low.layers["tests"].confidence == CONFIDENCE_LOW


def test_project_confidence_depends_on_evidence_quality_and_coverage():
    strong = [entry("frontend/a.tsx", "typescript"), entry("frontend/b.ts", "typescript"), entry("backend/A.java", "java"), entry("backend/B.java", "java")]
    assert analyze_architecture(strong).confidence == CONFIDENCE_HIGH

    # same strong evidence, but 3 of 7 files cannot be classified (57% coverage)
    diluted = strong + [entry(f"misc/m{i}.py", "python") for i in range(3)]
    assert analyze_architecture(diluted).confidence == CONFIDENCE_MEDIUM

    # one classified file among ten: not enough of the project is understood
    sparse = [entry("tests/test_a.py", "python")] + [entry(f"src/m{i}.py", "python") for i in range(9)]
    assert analyze_architecture(sparse).confidence == CONFIDENCE_LOW


def test_project_confidence_thresholds_are_inclusive_at_the_boundary():
    # exactly 75% coverage with perfect evidence is still HIGH
    files = [entry("backend/a.py", "python"), entry("backend/b.py", "python"), entry("backend/c.py", "python"), entry("misc/x.py", "python")]
    assert analyze_architecture(files).confidence == CONFIDENCE_HIGH


def test_weak_evidence_everywhere_never_produces_a_high_project_confidence():
    # full coverage but only weak/medium evidence: mean 2.0
    files = [entry("web/a.tsx", "typescript"), entry("services/B.java", "java"), entry("api/c.py", "python")]
    summary = analyze_architecture(files)
    assert summary.confidence == CONFIDENCE_MEDIUM

    name_only = analyze_architecture([entry("src/A.test.ts", "typescript"), entry("src/B.test.ts", "typescript")])
    assert name_only.confidence == CONFIDENCE_LOW


def test_unknown_layer_is_always_low_and_never_counts_as_detected():
    summary = analyze_architecture([entry("misc/a.py", "python"), entry("frontend/b.ts", "typescript")])
    assert summary.layers["unknown"].confidence == CONFIDENCE_LOW
    assert "unknown" not in summary.detected_layers


def test_every_classification_carries_evidence():
    summary = analyze_architecture([entry("frontend/a.tsx", "typescript"), entry("misc/b.py", "python"), entry("src/c.test.ts", "typescript")])
    assert all(item.evidence for item in summary.classifications)
    detail = summary.to_dict(include_files=True)["files"]
    assert {item["path"] for item in detail} == {"frontend/a.tsx", "misc/b.py", "src/c.test.ts"}
    assert "files" not in summary.to_dict()  # compact by default


# --------------------------------------------------------------- input forms


def test_module_result_dict_and_scanner_records_are_all_accepted(tmp_path: Path):
    (tmp_path / "frontend").mkdir()
    (tmp_path / "frontend" / "App.tsx").write_text("export const App = () => 1;\n")
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "A.java").write_text("public class A {}\n")
    analysis = analyze_project(tmp_path)

    from_objects = analyze_architecture(analysis.modules)
    from_dicts = analyze_architecture([m.to_dict() for m in analysis.modules])
    from_scanner = analyze_architecture(discover_source_files(tmp_path))  # no LOC available -> 0
    assert from_objects.to_dict() == from_dicts.to_dict()
    assert from_scanner.detected_layers == from_objects.detected_layers == ["frontend", "backend"]
    assert from_scanner.total_loc == 0 and from_objects.total_loc > 0


def test_a_record_without_a_path_or_language_is_rejected():
    with pytest.raises(ValueError):
        analyze_architecture([{"file_path": "a.py"}])
    with pytest.raises(ValueError):
        analyze_architecture([{"language": "python"}])


def test_classification_is_language_agnostic_and_never_mutates_its_input():
    record = entry("backend/x.cob", "cobol")
    before = dict(record)
    assert analyze_architecture([record]).layers["backend"].languages == {"cobol": 1}
    assert record == before


# ------------------------------ integration with the real scanner / pipeline


@pytest.fixture()
def fullstack_project(tmp_path: Path) -> Path:
    files = {
        "frontend/App.tsx": "export const App = (): number => {\n  return 1;\n};\n",
        "frontend/api.ts": "export const get = (url: string): string => url;\n",
        "backend/AuthService.java": (
            "public class AuthService {\n"
            "    public boolean login(String user) {\n"
            "        if (user == null) {\n            return false;\n        }\n"
            "        return user.length() > 2;\n    }\n}\n"
        ),
        "backend/PaymentController.java": (
            "public class PaymentController {\n"
            "    private AuthService auth = new AuthService();\n"
            "    public int pay(String user, int amount) {\n"
            "        if (!auth.login(user)) {\n            return 0;\n        }\n"
            "        return amount;\n    }\n}\n"
        ),
        "tests/test_auth.py": "def test_login():\n    assert True\n",
        "docs/notes.md": "# not source code\n",
    }
    for name, content in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return tmp_path


def test_full_stack_project_through_the_real_pipeline(fullstack_project: Path):
    analysis = analyze_project(fullstack_project)
    summary = analyze_architecture(analysis.modules)

    assert summary.detected_layers == ["frontend", "backend", "tests"]
    assert summary.languages_by_layer == {
        "frontend": {"typescript": 2},
        "backend": {"java": 2},
        "tests": {"python": 1},
    }
    assert summary.total_files == analysis.files_analyzed == 5  # the .md file is not source
    assert summary.total_loc == analysis.total_loc  # same LOC definition as the project total
    assert sorted(summary.languages) == analysis.languages  # languages come from the scanner
    assert summary.confidence == CONFIDENCE_HIGH
    assert summary.layer_of("backend/PaymentController.java") == "backend"
    assert summary.layer_of("frontend/api.ts") == "frontend"
    assert summary.layer_of("does/not/exist.py") is None


def test_parse_errors_do_not_hide_a_file_from_its_layer(tmp_path: Path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "backend" / "Broken.java").write_text("public class Broken {\n")  # unbalanced
    (tmp_path / "backend" / "Ok.java").write_text("public class Ok {}\n")
    analysis = analyze_project(tmp_path)
    assert analysis.files_with_errors == 1
    summary = analyze_architecture(analysis.modules)
    assert summary.files_by_layer == {"backend": 2}


def test_the_bundled_demo_projects_classify_without_error():
    root = Path(__file__).resolve().parents[2] / "sample_projects"
    for demo in sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("demo_")):
        analysis = analyze_project(demo)
        summary = analyze_architecture(analysis.modules)
        assert summary.total_files == analysis.files_analyzed
        assert summary.total_loc == analysis.total_loc
        assert sum(summary.files_by_layer.values()) == summary.total_files


# -------------------- existing behaviour is untouched (Phase 8A is additive)


def test_existing_analysis_output_shape_is_unchanged(fullstack_project: Path):
    analysis = analyze_project(fullstack_project)
    data = analysis.to_dict()
    assert set(data) == {"project", "summary", "modules"}  # no 'architecture' key was added
    assert set(data["project"]) == {"languages", "files", "classes", "functions"}
    assert set(data["modules"][0]) == {
        "file_name", "file_path", "language", "loc", "classes", "functions", "complexity",
        "maintainability_index", "coupling", "cohesion", "depth_of_inheritance",
        "duplication_percentage", "duplicated_lines", "parse_error",
    }


def test_architecture_does_not_define_its_own_language_detection():
    # Languages are a property of the scanner's table, not of this module.
    assert not any(name in vars(arch) for name in ("LANGUAGE_BY_EXT", "_resolve_language", "discover_source_files"))
    assert LANGUAGE_BY_EXT[".tsx"] == "typescript" and LANGUAGE_BY_EXT[".java"] == "java"
