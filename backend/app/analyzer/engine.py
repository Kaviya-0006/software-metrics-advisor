"""
Analysis engine.

Orchestrates the full Phase 2 pipeline for one uploaded project:

    scan -> parse each file -> per-file metrics -> project-wide metrics
    (inheritance registry, duplication) -> assemble module dicts ->
    assemble project summary

This is the only module that knows the *order* metrics must be computed
in (duplication and inheritance need every file's data up front; the rest
are per-file). Each individual metric's logic lives in analyzer/metrics/
and knows nothing about this orchestration.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from app.analyzer.metrics.cohesion import calculate_module_cohesion
from app.analyzer.metrics.complexity import (
    calculate_c_family_complexity,
    calculate_js_family_complexity,
    calculate_java_complexity,
    calculate_python_complexity,
)
from app.analyzer.metrics.coupling import calculate_coupling
from app.analyzer.metrics.duplication import calculate_project_duplication
from app.analyzer.metrics.inheritance import build_class_registry, calculate_class_dit
from app.analyzer.metrics.loc import LocResult, calculate_loc
from app.analyzer.metrics.testing import calculate_testing_score
from app.analyzer.metrics.maintainability import (
    calculate_c_family_maintainability,
    calculate_js_family_maintainability,
    calculate_java_maintainability,
    calculate_python_maintainability,
)
from app.analyzer.parser import ParsedModule, parse_file, uses_jsx
from app.analyzer.scanner import discover_source_files


@dataclass
class ModuleResult:
    file_name: str
    file_path: str
    language: str
    loc: LocResult
    classes: int
    functions: int
    complexity_total: int
    complexity_average: float
    complexity_maximum: int
    maintainability_index: float | None
    coupling: int
    cohesion: float | None
    depth_of_inheritance: int | None
    duplication_percentage: float
    duplicated_lines: int
    parse_error: str | None

    def to_dict(self) -> dict:
        return {
            "file_name": self.file_name,
            "file_path": self.file_path,
            "language": self.language,
            "loc": {
                "total": self.loc.total,
                "code": self.loc.code,
                "comments": self.loc.comments,
                "blank": self.loc.blank,
            },
            "classes": self.classes,
            "functions": self.functions,
            "complexity": {
                "total": self.complexity_total,
                "average": self.complexity_average,
                "maximum": self.complexity_maximum,
            },
            "maintainability_index": self.maintainability_index,
            "coupling": self.coupling,
            "cohesion": self.cohesion,
            "depth_of_inheritance": self.depth_of_inheritance,
            "duplication_percentage": self.duplication_percentage,
            "duplicated_lines": self.duplicated_lines,
            "parse_error": self.parse_error,
        }


@dataclass
class ProjectAnalysis:
    modules: list[ModuleResult]
    files_analyzed: int
    languages: list[str]
    total_classes: int
    total_functions: int
    total_loc: int
    total_complexity: int
    average_complexity: float
    average_maintainability: float | None
    average_coupling: float
    duplication_percentage: float
    duplicated_lines: int
    files_with_errors: int
    testing_score: float | None = None
    test_files: int = 0
    production_files: int = 0
    # Opt-in (see `analyze_project(retain_parsed=True)`): the ParsedModules the
    # metrics were computed from, i.e. source text + language + parse tree per
    # file. Empty by default, never serialized, ignored by comparison. It lets
    # later analyses (Phase 8B API-endpoint detection) reuse the single parse
    # instead of re-reading the extracted files, which the upload flow deletes
    # as soon as the request finishes.
    parsed_modules: list[ParsedModule] = field(default_factory=list, repr=False, compare=False)

    def to_dict(self) -> dict:
        return {
            "project": {
                "languages": self.languages,
                "files": self.files_analyzed,
                "classes": self.total_classes,
                "functions": self.total_functions,
            },
            "summary": {
                "total_loc": self.total_loc,
                "total_complexity": self.total_complexity,
                "average_complexity": self.average_complexity,
                "average_maintainability": self.average_maintainability,
                "average_coupling": self.average_coupling,
                "duplication_percentage": self.duplication_percentage,
                "duplicated_lines": self.duplicated_lines,
                "files_with_errors": self.files_with_errors,
                "testing_score": self.testing_score,
                "test_files": self.test_files,
                "production_files": self.production_files,
            },
            "modules": [m.to_dict() for m in self.modules],
        }


def _analyze_single_module(module: ParsedModule) -> tuple[ModuleResult, int]:
    """
    Compute every per-file metric for one already-parsed module. Returns
    the ModuleResult plus its complexity total (needed by the caller for
    the Java MI calculation, which depends on complexity).

    A file that failed to parse still gets a ModuleResult — with all
    numeric metrics as zero/None and `parse_error` set — rather than being
    silently dropped, so the API response accounts for every uploaded file.
    """
    if module.parse_error is not None:
        empty_loc = LocResult(total=0, code=0, comments=0, blank=0)
        return (
            ModuleResult(
                file_name=module.source_file.file_name,
                file_path=module.source_file.relative_path,
                language=module.language,
                loc=empty_loc,
                classes=0,
                functions=0,
                complexity_total=0,
                complexity_average=0.0,
                complexity_maximum=0,
                maintainability_index=None,
                coupling=0,
                cohesion=None,
                depth_of_inheritance=None,
                duplication_percentage=0.0,
                duplicated_lines=0,
                parse_error=module.parse_error,
            ),
            0,
        )

    loc = calculate_loc(
        module.language, module.source_text, jsx=uses_jsx(module.source_file.file_name)
    )

    if module.language == "python":
        complexity = calculate_python_complexity(module.source_text)
        mi = calculate_python_maintainability(module.source_text)
        function_count = len(module.functions) + sum(
            len(c.method_names) for c in module.classes
        )
    elif module.language in ("c", "cpp"):
        complexity = calculate_c_family_complexity(module.raw_tree)
        mi = calculate_c_family_maintainability(module.source_text, complexity.total, loc)
        # Number of function definitions (free + member) found in the file.
        function_count = len(module.raw_tree.functions)
    elif module.language in ("javascript", "typescript"):
        complexity = calculate_js_family_complexity(module.raw_tree)
        mi = calculate_js_family_maintainability(module.raw_tree, complexity.total, loc)
        # Number of function definitions (free, arrow, callbacks, methods, constructors).
        function_count = len(module.raw_tree.functions)
    else:  # java
        complexity = calculate_java_complexity(module.raw_tree)
        mi = calculate_java_maintainability(module.source_text, complexity.total, loc)
        function_count = sum(len(c.method_names) for c in module.classes)

    coupling = calculate_coupling(module)
    cohesion = calculate_module_cohesion(module)

    result = ModuleResult(
        file_name=module.source_file.file_name,
        file_path=module.source_file.relative_path,
        language=module.language,
        loc=loc,
        classes=len(module.classes),
        functions=function_count,
        complexity_total=complexity.total,
        complexity_average=complexity.average,
        complexity_maximum=complexity.maximum,
        maintainability_index=mi,
        coupling=coupling,
        cohesion=cohesion,
        depth_of_inheritance=None,  # filled in by the caller (project-wide)
        duplication_percentage=0.0,  # filled in by the caller (project-wide)
        duplicated_lines=0,
        parse_error=None,
    )
    return result, complexity.total


def analyze_project(project_root: Path, *, retain_parsed: bool = False) -> ProjectAnalysis:
    """Run the full Phase 2 pipeline against an already-extracted project
    directory and return a ProjectAnalysis with every module scored.

    `retain_parsed=True` additionally keeps the parsed modules on
    `ProjectAnalysis.parsed_modules` (default: not retained, so existing
    callers are completely unaffected)."""

    source_files = discover_source_files(project_root)
    parsed_modules = [parse_file(sf) for sf in source_files]

    module_results: list[ModuleResult] = []
    for parsed in parsed_modules:
        result, _ = _analyze_single_module(parsed)
        module_results.append(result)

    # --- Project-wide pass 1: Depth of Inheritance ------------------------
    # Requires every class across every file to be known first.
    class_registry = build_class_registry(parsed_modules)
    for parsed, result in zip(parsed_modules, module_results):
        if result.parse_error is not None or not parsed.classes:
            continue
        if parsed.language == "c":
            continue  # C has no class inheritance — keep the neutral default (None)
        depths = [calculate_class_dit(c.name, class_registry) for c in parsed.classes]
        result.depth_of_inheritance = round(sum(depths) / len(depths), 2) if depths else None

    # --- Project-wide pass 2: Duplication ----------------------------------
    # Requires every file's source text at once.
    file_sources = {
        parsed.source_file.relative_path: parsed.source_text
        for parsed in parsed_modules
        if parsed.parse_error is None
    }
    project_dup, per_file_dup = calculate_project_duplication(file_sources)
    for result in module_results:
        dup = per_file_dup.get(result.file_path)
        if dup is not None:
            result.duplication_percentage = dup.duplication_percentage
            result.duplicated_lines = dup.duplicated_lines

    # --- Project-level summary --------------------------------------------
    successful = [r for r in module_results if r.parse_error is None]
    languages = sorted({r.language for r in module_results})

    total_loc = sum(r.loc.total for r in successful)
    total_complexity = sum(r.complexity_total for r in successful)
    total_classes = sum(r.classes for r in successful)
    total_functions = sum(r.functions for r in successful)

    complexities = [r.complexity_average for r in successful if r.complexity_total > 0]
    average_complexity = round(sum(complexities) / len(complexities), 2) if complexities else 0.0

    mi_values = [r.maintainability_index for r in successful if r.maintainability_index is not None]
    average_maintainability = round(sum(mi_values) / len(mi_values), 2) if mi_values else None

    coupling_values = [r.coupling for r in successful]
    average_coupling = round(sum(coupling_values) / len(coupling_values), 2) if coupling_values else 0.0

    testing_score, test_files, production_files = calculate_testing_score(
        [sf.relative_path for sf in source_files]
    )

    return ProjectAnalysis(
        modules=module_results,
        files_analyzed=len(module_results),
        languages=languages,
        total_classes=total_classes,
        total_functions=total_functions,
        total_loc=total_loc,
        total_complexity=total_complexity,
        average_complexity=average_complexity,
        average_maintainability=average_maintainability,
        average_coupling=average_coupling,
        duplication_percentage=project_dup.duplication_percentage,
        duplicated_lines=project_dup.duplicated_lines,
        files_with_errors=len(module_results) - len(successful),
        testing_score=testing_score,
        test_files=test_files,
        production_files=production_files,
        parsed_modules=parsed_modules if retain_parsed else [],
    )
