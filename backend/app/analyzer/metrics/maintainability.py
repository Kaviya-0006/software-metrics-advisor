"""
Metric: Maintainability Index (MI).

We use the same formula for both languages — the classic Oman/Hakanen
formulation refined by the Coleman/Software Productivity Research (SPR)
"MI derivative" that the original Microsoft/Visual Studio Code Metrics
feature and radon both use:

    MI_raw = 171
             - 5.2  * ln(HalsteadVolume)
             - 0.23 * CyclomaticComplexity
             - 16.2 * ln(SLOC)
             + 50   * sin( sqrt(2.46 * comment_ratio_in_radians) )

    MI = clamp( MI_raw * 100 / 171,  0,  100 )

Where:
  - HalsteadVolume = N * log2(n), N = total operators + operands,
    n = unique operators + unique operands (Halstead's program-length /
    vocabulary measures — see below for how tokens are classified).
  - CyclomaticComplexity = total McCabe complexity for the file (sum over
    all functions/methods), from analyzer/metrics/complexity.py.
  - SLOC = source (non-blank, non-comment) lines of code, from
    analyzer/metrics/loc.py.
  - comment_ratio = comment_lines / SLOC (used inside sqrt(2.46 * radians)
    as in the original formula, NOT a raw percentage).

Output range: 0-100. Higher = more maintainable. Conventional interpretation
bands (used e.g. by Visual Studio): 0-9 low, 10-19 moderate, 20-100 high.
This project does not classify a module by this band yet — normalization
into the Phase 3 Health Score is a later phase's job.

Python: radon's `mi_visit` implements exactly this formula (verified by
reading radon's source — see docs/metrics.md), so we call it directly
rather than re-deriving Halstead metrics ourselves.

Java: no equivalent library exists in this stack, so we compute the same
four inputs ourselves:
  - SLOC/comments from analyzer/metrics/loc.py's calculate_java_loc
  - Cyclomatic complexity from analyzer/metrics/complexity.py
  - Halstead volume from the javalang token stream, classifying tokens as
    operators (keywords, modifiers, basic types, separators, operators,
    annotations) or operands (identifiers, literals) — a standard
    simplification documented in docs/metrics.md; it does not distinguish,
    e.g., unary vs binary operator context the way a hand-tuned Halstead
    analyzer for Java might.
Then apply the identical mi_compute formula radon uses, so Python and Java
results are produced by one consistent computation.
"""

from __future__ import annotations

import math
import re

import javalang
from radon.metrics import mi_visit as radon_mi_visit

from app.analyzer.cfamily import lex_source
from app.analyzer.metrics.loc import LocResult

_OPERAND_TOKEN_TYPES = (javalang.tokenizer.Identifier, javalang.tokenizer.Literal)
_OPERATOR_TOKEN_TYPES = (
    javalang.tokenizer.Keyword,
    javalang.tokenizer.Modifier,
    javalang.tokenizer.BasicType,
    javalang.tokenizer.Separator,
    javalang.tokenizer.Operator,
    javalang.tokenizer.Annotation,
)


def _java_halstead_volume(source_text: str) -> float:
    try:
        tokens = list(javalang.tokenizer.tokenize(source_text))
    except (javalang.tokenizer.LexerError, StopIteration):
        return 0.0

    operators: dict[str, int] = {}
    operands: dict[str, int] = {}

    for tok in tokens:
        if isinstance(tok, _OPERAND_TOKEN_TYPES):
            operands[tok.value] = operands.get(tok.value, 0) + 1
        elif isinstance(tok, _OPERATOR_TOKEN_TYPES):
            operators[tok.value] = operators.get(tok.value, 0) + 1

    n1, n2 = len(operators), len(operands)  # unique operators / operands
    big_n1 = sum(operators.values())  # total operator occurrences
    big_n2 = sum(operands.values())  # total operand occurrences

    vocabulary = n1 + n2
    length = big_n1 + big_n2
    if vocabulary <= 0 or length <= 0:
        return 0.0

    return length * math.log2(vocabulary)


def _mi_compute(halstead_volume: float, complexity: int, sloc: int, comment_lines: int) -> float:
    """
    Same formula radon's `mi_compute` uses (mirrored here so Java doesn't
    need to depend on radon internals). See module docstring for the
    formula and citations.
    """
    if halstead_volume <= 0 or sloc <= 0:
        return 100.0

    comment_ratio_percent = (comment_lines / sloc) * 100 if sloc else 0.0

    sloc_scale = math.log(sloc)
    volume_scale = math.log(halstead_volume)
    comments_scale = math.sqrt(2.46 * math.radians(comment_ratio_percent))

    mi_raw = (
        171
        - 5.2 * volume_scale
        - 0.23 * complexity
        - 16.2 * sloc_scale
        + 50 * math.sin(comments_scale)
    )
    return min(max(0.0, mi_raw * 100 / 171.0), 100.0)


def calculate_python_maintainability(source_text: str) -> float:
    """Maintainability Index for a Python file, 0-100."""
    return round(radon_mi_visit(source_text, multi=True), 2)


def calculate_java_maintainability(
    source_text: str, complexity_total: int, loc: LocResult
) -> float:
    """Maintainability Index for a Java file, 0-100, using the identical
    formula radon applies to Python (see module docstring)."""
    volume = _java_halstead_volume(source_text)
    mi = _mi_compute(
        halstead_volume=volume,
        complexity=complexity_total,
        sloc=loc.code,
        comment_lines=loc.comments,
    )
    return round(mi, 2)


# --- C / C++ ---------------------------------------------------------------
# Same four inputs and the identical `_mi_compute` formula as Java. Halstead
# tokens come from a small regex tokenizer over the comment-free source:
# keywords, operators and punctuation are "operators"; identifiers and
# string/char/number literals are "operands".

_C_KEYWORDS = frozenset(
    "alignas alignof and and_eq asm auto bitand bitor bool break case catch char "
    "class compl const consteval constexpr constinit const_cast continue "
    "decltype default delete do double dynamic_cast else enum explicit export "
    "extern false final float for friend goto if inline int long mutable "
    "namespace new noexcept not not_eq nullptr operator or or_eq override "
    "private protected public register reinterpret_cast return short signed "
    "sizeof static static_assert static_cast struct switch template this "
    "thread_local throw true try typedef typeid typename union unsigned using "
    "virtual void volatile while xor xor_eq".split()
)

_C_TOKEN_RE = re.compile(
    r"""
    (?P<string>"(?:\\.|[^"\\\n])*")
  | (?P<char>'(?:\\.|[^'\\\n])*')
  | (?P<number>0[xX][0-9a-fA-F']+[uUlL]*|\d[\d']*(?:\.\d*)?(?:[eE][+-]?\d+)?[fFuUlL]*|\.\d+(?:[eE][+-]?\d+)?[fFlL]?)
  | (?P<ident>[A-Za-z_]\w*)
  | (?P<op><<=|>>=|<=>|\.\.\.|->\*|->|::|\+\+|--|<<|>>|<=|>=|==|!=|&&|\|\||\+=|-=|\*=|/=|%=|&=|\|=|\^=|\S)
    """,
    re.VERBOSE,
)


def _c_family_halstead_volume(source_text: str) -> float:
    code = lex_source(source_text).no_comments
    operators: dict[str, int] = {}
    operands: dict[str, int] = {}

    for match in _C_TOKEN_RE.finditer(code):
        kind = match.lastgroup
        text = match.group(0)
        if kind in ("string", "char", "number"):
            operands[text] = operands.get(text, 0) + 1
        elif kind == "ident":
            bucket = operators if text in _C_KEYWORDS else operands
            bucket[text] = bucket.get(text, 0) + 1
        else:
            operators[text] = operators.get(text, 0) + 1

    vocabulary = len(operators) + len(operands)
    length = sum(operators.values()) + sum(operands.values())
    if vocabulary <= 0 or length <= 0:
        return 0.0
    return length * math.log2(vocabulary)


def calculate_c_family_maintainability(
    source_text: str, complexity_total: int, loc: LocResult
) -> float:
    """Maintainability Index for a C/C++ file, 0-100 (same formula as Java)."""
    volume = _c_family_halstead_volume(source_text)
    mi = _mi_compute(
        halstead_volume=volume,
        complexity=complexity_total,
        sloc=loc.code,
        comment_lines=loc.comments,
    )
    return round(mi, 2)


# --- JavaScript / TypeScript -----------------------------------------------
# Same four inputs and the identical `_mi_compute` formula as the other
# languages. Halstead tokens come from the JS/TS scanner's own cleaned token
# stream (comments, regex bodies and JSX text are already excluded):
# keywords and punctuators are "operators"; identifiers, numbers and string /
# template literals are "operands".

_JS_KEYWORDS = frozenset(
    "abstract as async await break case catch class const continue debugger declare "
    "default delete do else enum export extends finally for from function get if "
    "implements import in instanceof interface is keyof let namespace new of "
    "override private protected public readonly return set static super switch "
    "this throw try type typeof var void while with yield".split()
)


def _js_family_halstead_volume(tree) -> float:
    operators: dict[str, int] = {}
    operands: dict[str, int] = {}

    for kind, text, start, end in zip(tree.kinds, tree.texts, tree.starts, tree.ends):
        if kind == "str":
            literal = tree.no_comments[start:end] if text != "`" else "`"
            operands[literal] = operands.get(literal, 0) + 1
        elif kind == "num":
            operands[text] = operands.get(text, 0) + 1
        elif kind == "id":
            bucket = operators if text in _JS_KEYWORDS else operands
            bucket[text] = bucket.get(text, 0) + 1
        else:
            operators[text] = operators.get(text, 0) + 1

    vocabulary = len(operators) + len(operands)
    length = sum(operators.values()) + sum(operands.values())
    if vocabulary <= 0 or length <= 0:
        return 0.0
    return length * math.log2(vocabulary)


def calculate_js_family_maintainability(tree, complexity_total: int, loc: LocResult) -> float:
    """Maintainability Index for a JS/TS file, 0-100 (same formula as Java)."""
    mi = _mi_compute(
        halstead_volume=_js_family_halstead_volume(tree),
        complexity=complexity_total,
        sloc=loc.code,
        comment_lines=loc.comments,
    )
    return round(mi, 2)
