"""
Tests for the Depth of Inheritance (DIT) metric.
"""

from app.analyzer.metrics.inheritance import build_class_registry, calculate_class_dit
from app.analyzer.parser import ParsedClass, ParsedModule
from app.analyzer.scanner import SourceFile


def _fake_module(classes: list[ParsedClass]) -> ParsedModule:
    sf = SourceFile(
        absolute_path=None, relative_path="fake.py", file_name="fake.py",
        language="python", size_bytes=0,
    )
    return ParsedModule(source_file=sf, language="python", source_text="", classes=classes)


def test_dit_three_level_chain_matches_worked_example():
    # Base -> Service -> PaymentService, DIT(PaymentService) == 2
    classes = [
        ParsedClass(name="Base", base_name=None),
        ParsedClass(name="Service", base_name="Base"),
        ParsedClass(name="PaymentService", base_name="Service"),
    ]
    registry = build_class_registry([_fake_module(classes)])
    assert calculate_class_dit("PaymentService", registry) == 2
    assert calculate_class_dit("Service", registry) == 1
    assert calculate_class_dit("Base", registry) == 0


def test_dit_no_base_is_zero():
    classes = [ParsedClass(name="Standalone", base_name=None)]
    registry = build_class_registry([_fake_module(classes)])
    assert calculate_class_dit("Standalone", registry) == 0


def test_dit_unresolved_external_base_counts_as_one():
    classes = [ParsedClass(name="MyException", base_name="Exception")]
    registry = build_class_registry([_fake_module(classes)])
    assert calculate_class_dit("MyException", registry) == 1


def test_dit_handles_cyclic_inheritance_without_hanging():
    classes = [
        ParsedClass(name="A", base_name="B"),
        ParsedClass(name="B", base_name="A"),
    ]
    registry = build_class_registry([_fake_module(classes)])
    # Must terminate — the exact depth is less important than not hanging.
    result = calculate_class_dit("A", registry)
    assert isinstance(result, int)
    assert result >= 0


def test_registry_spans_multiple_modules():
    module_a = _fake_module([ParsedClass(name="Base", base_name=None)])
    module_b = _fake_module([ParsedClass(name="Child", base_name="Base")])
    registry = build_class_registry([module_a, module_b])
    assert calculate_class_dit("Child", registry) == 1
