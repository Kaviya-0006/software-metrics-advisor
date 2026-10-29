"""
Metric: Cyclomatic Complexity.

Definition (McCabe, 1976): the number of linearly independent paths
through a function's control-flow graph. The standard practical formula
used here is:

    CC = 1 + (number of decision points)

where a "decision point" is any construct that can branch execution:
if/elif, for, while, except/catch, boolean and/or short-circuit operators,
case labels in a switch/match, and ternary/conditional expressions.

Python: delegated to `radon.complexity.cc_visit`, which implements exactly
this formula over the Python AST — a well-established, independently
maintained implementation rather than a bespoke one.

Java: no equivalent lightweight library exists in this stack, so we
implement the same formula directly by walking the javalang AST and
counting decision-point node types per method. This is documented in
docs/metrics.md so it can be defended in the viva.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import javalang
from radon.complexity import cc_visit as radon_cc_visit
from radon.visitors import Function as RadonFunction


@dataclass
class FunctionComplexity:
    name: str
    complexity: int
    owner_class: str | None = None


@dataclass
class ComplexityResult:
    total: int
    average: float
    maximum: int
    per_function: list[FunctionComplexity] = field(default_factory=list)


def _summarize(per_function: list[FunctionComplexity]) -> ComplexityResult:
    if not per_function:
        # A file with no functions/methods has no branchable units. We
        # report 0 rather than a fabricated 1, since there is nothing to
        # measure a path count for.
        return ComplexityResult(total=0, average=0.0, maximum=0, per_function=[])

    values = [f.complexity for f in per_function]
    total = sum(values)
    return ComplexityResult(
        total=total,
        average=round(total / len(values), 2),
        maximum=max(values),
        per_function=per_function,
    )


def calculate_python_complexity(source_text: str) -> ComplexityResult:
    blocks = radon_cc_visit(source_text)
    per_function = [
        FunctionComplexity(
            name=block.name,
            complexity=block.complexity,
            owner_class=getattr(block, "classname", None),
        )
        for block in blocks
        if isinstance(block, RadonFunction)  # skip aggregate Class entries
    ]
    return _summarize(per_function)


# Java decision-point node types that each add one branch, per the
# McCabe formula. BinaryOperation is only counted when the operator is a
# short-circuit logical operator (&&, ||), since those introduce an
# additional path just like a boolean condition in Python.
_JAVA_SIMPLE_DECISION_TYPES = (
    javalang.tree.IfStatement,
    javalang.tree.ForStatement,
    javalang.tree.WhileStatement,
    javalang.tree.DoStatement,
    javalang.tree.CatchClause,
    javalang.tree.TernaryExpression,
)


def _count_java_decision_points(method_node) -> int:
    decisions = 0

    # NOTE: javalang's Node.filter() only matches a single type (it does
    # `isinstance(pattern, type)`, which is False for a tuple), so passing
    # a tuple of types silently matches nothing. We walk the subtree once
    # ourselves and check membership directly instead.
    for _, node in method_node:
        if isinstance(node, _JAVA_SIMPLE_DECISION_TYPES):
            decisions += 1
        elif isinstance(node, javalang.tree.SwitchStatementCase):
            # A `case` label's `.case` is a list of matched values, and is
            # an EMPTY list (not None) for the `default:` label — so we
            # must check truthiness, not `is not None`, to exclude it.
            if node.case:
                decisions += 1
        elif isinstance(node, javalang.tree.BinaryOperation):
            if node.operator in ("&&", "||"):
                decisions += 1

    return decisions


def calculate_java_complexity(tree) -> ComplexityResult:
    per_function: list[FunctionComplexity] = []

    for _, class_node in tree.filter(javalang.tree.ClassDeclaration):
        for method in class_node.methods:
            if method.body is None:
                continue  # abstract/interface method — no body to measure
            complexity = 1 + _count_java_decision_points(method)
            per_function.append(
                FunctionComplexity(
                    name=method.name,
                    complexity=complexity,
                    owner_class=class_node.name,
                )
            )

    return _summarize(per_function)


# C / C++: same convention (CC = 1 + decision points per function). The
# lexer pass has already blanked comments, string/char literals and
# preprocessor directives, so these tokens are only matched in real code.
# `else`, `default:` and `do` add no branch of their own (the `while` that
# closes a do-while is the one counted decision), matching the Java rules.
_C_DECISION_RE = re.compile(r"\b(?:if|for|while|case|catch)\b|&&|\|\||\?")


def calculate_c_family_complexity(tree) -> ComplexityResult:
    """Complexity of every function *definition* in a CFamilyTree
    (free functions and member functions, including constructors)."""
    per_function = [
        FunctionComplexity(
            name=func.name,
            complexity=1 + len(_C_DECISION_RE.findall(func.body)),
            owner_class=func.owner_class,
        )
        for func in tree.functions
    ]
    return _summarize(per_function)


# JavaScript / TypeScript: same convention (CC = 1 + decision points per
# function). Counted over the cleaned token stream, so keywords inside
# strings, templates, comments, regex bodies and JSX text are never seen.
#   +1: if, for (incl. for-of/in/await), while (incl. the one closing a
#       do-while), case, catch, &&, ||, ??, ternary ?:
#   +0: else, default, do, try, optional chaining ?., and `?` used as a TS
#       optional marker (`a?: T`, `(a?) =>`).
_JS_DECISION_WORDS = frozenset(["if", "for", "while", "case", "catch"])


def _is_catch_clause(kinds: list[str], texts: list[str], k: int) -> bool:
    """A `catch` token is a clause only when the `}` right before it closes a
    block opened by `try`. This excludes a *method named* `catch` (thenable
    classes such as zod's ZodType, `{ catch(e) {...} }`), which has the same
    `catch (x) {` shape but follows an ordinary member."""
    if k == 0 or texts[k - 1] != "}":
        return False
    depth = 0
    j = k - 1
    while j >= 0:
        if kinds[j] == "punct":
            if texts[j] == "}":
                depth += 1
            elif texts[j] == "{":
                depth -= 1
                if depth == 0:
                    return j > 0 and texts[j - 1] == "try"
        j -= 1
    return False


def _js_decision_points(kinds: list[str], texts: list[str], start: int, end: int) -> int:
    count = 0
    for k in range(start, end):
        text, kind = texts[k], kinds[k]
        if kind == "id":
            if text not in _JS_DECISION_WORDS:
                continue
            prev = texts[k - 1] if k > 0 else ""
            nxt = texts[k + 1] if k + 1 < len(texts) else ""
            if prev in (".", "?."):
                continue  # property access such as promise.catch(...)
            if nxt == ":" and prev in ("{", ","):
                continue  # object key such as { if: 1 }
            if text == "catch" and not _is_catch_clause(kinds, texts, k):
                continue  # a method/member named `catch`, not a catch clause
            count += 1
        elif kind == "punct":
            if text in ("&&", "||", "??"):
                count += 1
            elif text == "?":
                nxt = texts[k + 1] if k + 1 < len(texts) else ""
                if nxt in (":", ")", ","):
                    continue  # TS optional marker, not a ternary
                count += 1
    return count


def calculate_js_family_complexity(tree) -> ComplexityResult:
    """Complexity of every function *definition* in a JsFamilyTree (free
    functions, arrow functions, callbacks, methods and constructors). Nested
    functions and callbacks are counted inside their enclosing function."""
    per_function = [
        FunctionComplexity(
            name=func.name,
            complexity=1 + _js_decision_points(tree.kinds, tree.texts, func.tok_start, func.tok_end),
            owner_class=func.owner_class,
        )
        for func in tree.functions
    ]
    return _summarize(per_function)
