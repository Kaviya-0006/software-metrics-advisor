# Software Metrics — Definitions, Formulas, and Limitations

This document explains every metric implemented in Phase 2:
what it measures, exactly how it is calculated, what it means, and what
its known limitations are. It exists so every number the system produces
can be defended in the viva with "here is exactly how we calculated that."

Implementation locations: `backend/app/analyzer/metrics/*.py`.

For how these raw metrics are turned into the Project Health Score and
per-module risk scores, see `docs/health-score.md` (Phase 3).

---

## 1. Lines of Code (LOC)

**Definition.** Physical lines of a source file, split into:
`total`, `code`, `comments`, `blank`, where `total = code + comments + blank`.

**Calculation method.**
- **Python** (`metrics/loc.py: calculate_python_loc`): uses `radon.raw.analyze`,
  which classifies every physical line against the Python grammar. Radon's
  `sloc` (source lines) and `multi` (lines inside a multi-line/triple-quoted
  string) are summed into `code`; `comments` and `blank` are used as-is.
  Rationale for folding `multi` into `code`: radon cannot reliably tell a
  docstring from an ordinary triple-quoted string literal, so we treat
  those lines as code rather than guessing.
- **Java** (`metrics/loc.py: calculate_java_loc`): no equivalent library
  exists in this stack, so a small state-machine line classifier is
  implemented directly: tracks whether we're inside a `/* ... */` block
  comment, and classifies each line as blank / `//` comment / block
  comment / code accordingly.

**Input:** raw source text. **Output:** four integers per file.

**Interpretation.** `code` is the number that matters for size-based
comparisons (e.g. normalizing complexity per line later). We deliberately
do **not** treat total file length as "LOC" — a heavily-commented or
blank-heavy file would otherwise look bigger than it really is.

**Limitations.**
- Java: a `/* ... */` comment that shares a line with real code (e.g.
  `int x = 1; /* note */`) is counted as one comment line, not
  code+comment — a documented simplification.
- Python: triple-quoted strings used as data (not docstrings) are counted
  as code, which is technically correct but may inflate "documentation"
  intuitions if someone expected all triple-quoted text to count as
  comments.

---

## 2. Number of Classes / Number of Functions

**Definition.** `classes` = count of class declarations in the file.
`functions` = count of callable units: free functions + methods (Python),
or methods only (Java has no free functions).

**Calculation method.** Directly from the parser (`parser.py`): Python via
`ast.ClassDef` / `ast.FunctionDef` walking; Java via `javalang`'s
`ClassDeclaration.methods`.

**Limitations.** Nested classes and nested functions are counted (the AST
walk is not restricted to top level for classes), which is intentional —
they are still real classes/functions a developer must maintain.

---

## 3. Cyclomatic Complexity

**Definition (McCabe, 1976).** The number of linearly independent paths
through a function's control-flow graph.

**Formula:** `CC = 1 + (number of decision points)`, where a decision
point is any construct that can branch execution.

**Calculation method.**
- **Python** (`metrics/complexity.py: calculate_python_complexity`): uses
  `radon.complexity.cc_visit`, an independently maintained implementation
  of the same McCabe formula over the Python AST.
- **Java** (`calculate_java_complexity`): implemented directly by walking
  the `javalang` AST per method and counting: `if`, `for`, `while`,
  `do-while`, `catch`, ternary (`?:`) expressions, non-default `case`
  labels in a `switch`, and short-circuit boolean operators (`&&`, `||`).

  **Implementation note (bug found and fixed during development):**
  `javalang`'s `Node.filter()` only accepts a single node type — passing a
  tuple silently matches nothing (it does `isinstance(pattern, type)`,
  which is `False` for a tuple). The Java complexity walker therefore
  walks each method's subtree once and checks `isinstance(node, TYPES)`
  directly rather than relying on `.filter()` with a tuple. Also: a
  `default:` switch label has `.case == []` (an empty list), which is
  falsy but **not** `None` — the counter checks truthiness
  (`if node.case:`), not `is not None`, to correctly exclude `default`
  from the branch count.

**Calculated at:** function/method level, then summed (`total`), averaged
(`average`), and maxed (`maximum`) per file. Project-level `average_complexity`
in the summary is the mean of per-file averages.

**Interpretation.** Higher = more independent paths = harder to test
(more test cases needed for full path coverage) and harder to reason
about. This project does **not** classify a value as "high/risky" — that
threshold-based judgment belongs to Phase 3.

**Verification.** `tests/test_complexity.py` includes hand-traced examples
(e.g. an `if` + `for` + inner `if` + `||` gives exactly CC=5) that assert
exact expected values, not just "some positive number."

---

## 4. Maintainability Index (MI)

**Definition.** A composite 0-100 score combining code volume, complexity,
and comment density into a single maintainability estimate. This project
uses the classic Oman/Hakanen formulation (as refined by the SEI/Coleman
"MI derivative", the same formula radon and the original Microsoft Visual
Studio Code Metrics feature use):

```
MI_raw = 171 - 5.2*ln(V) - 0.23*CC - 16.2*ln(SLOC) + 50*sin(sqrt(2.46*CM_rad))
MI     = clamp( MI_raw * 100 / 171,  0,  100 )
```

Where:
- `V` = Halstead Volume = `N * log2(n)`, with `N` = total operator +
  operand occurrences, `n` = unique operators + unique operands.
- `CC` = total cyclomatic complexity for the file (see §3).
- `SLOC` = source lines of code (see §1).
- `CM_rad` = `radians(comment_lines / SLOC * 100)` — the comment
  percentage, converted to radians for the `sin(sqrt(...))` term (this is
  exactly how the original formula and radon apply it).

**Calculation method.**
- **Python** (`metrics/maintainability.py: calculate_python_maintainability`):
  calls `radon.metrics.mi_visit` directly — verified by reading radon's
  source (`mi_compute`) to confirm it implements the formula above exactly.
- **Java** (`calculate_java_maintainability`): no equivalent library
  exists, so the four inputs are computed ourselves and fed into a local
  re-implementation of the identical `mi_compute` formula (`_mi_compute`
  in the same file), so both languages produce comparable MI values from
  one consistent computation:
  - `SLOC` / comment lines: from `metrics/loc.py`'s `calculate_java_loc`.
  - `CC`: from `metrics/complexity.py`'s `calculate_java_complexity`.
  - `V` (Halstead Volume): computed from the `javalang` token stream.
    Tokens are classified as **operators** (`Keyword`, `Modifier`,
    `BasicType`, `Separator`, `Operator`, `Annotation`) or **operands**
    (`Identifier`, `Literal` and its subtypes). This operator/operand
    split is a standard simplification used in academic Halstead
    implementations for languages without a dedicated Halstead library —
    it does not distinguish operator arity or context (e.g. unary vs.
    binary `-`), which a hand-tuned analyzer might.

**Output range:** 0-100. Higher = more maintainable.

**Interpretation (conventional bands, informational only — not used for
classification in this phase):** 0-9 low, 10-19 moderate, 20-100 high
maintainability (the bands historically used by Visual Studio's Code
Metrics feature).

**Verification.** `tests/test_maintainability.py` checks the value stays
in `[0, 100]` and decreases monotonically as branching/complexity
increases, for both languages.

---

## 5. Coupling

**IMPORTANT — read this before citing "coupling" as CBO in the viva.**

This project measures a **static, import/reference-based coupling count**,
**not** the full Chidamber & Kemerer Coupling Between Objects (CBO)
metric. True CBO requires resolving every type actually *used* in a
class's body — field types, parameter types, return types, local variable
types, casts, calls on other objects — against a fully built project type
model. That requires a real compiler/type-resolution pass, out of scope
for this static analyzer.

**What is actually computed** (`metrics/coupling.py`), per module:

- **Python** (`calculate_python_coupling`): count of distinct top-level
  modules/packages imported (`import x` / `from x import y`, counting the
  root package name once). This approximates Robert Martin's *efferent
  coupling (Ce)* — how many other things this module depends on.
- **Java** (`calculate_java_coupling`): count of distinct external types
  referenced, as the union of (a) imported type names and (b) type names
  in a class's `extends`/`implements` clauses. Closer in spirit to CBO
  than the Python count (captures inheritance-based coupling too), but
  still undercounts true CBO for types referenced only through field
  types, method parameters, or local variables without an import (e.g. a
  same-package class).

**Output:** a single integer `coupling` per module.

**Limitations.** Both counts are intentionally simple and cheap. They are
directionally useful (a file importing/extending many things is more
entangled with the rest of the codebase than one that imports nothing)
but should not be quoted as "CBO = N" without this caveat.

---

## 6. Cohesion

**Variant used: LCOM1** (Chidamber & Kemerer's original 1991 "Lack of
Cohesion Of Methods" definition), computed per class:

```
P = number of method pairs sharing NO common instance field
Q = number of method pairs sharing AT LEAST ONE common field
LCOM1 = max(P - Q, 0)
```

**Calculation method** (`metrics/cohesion.py`): for each method, collect
the set of instance-field names it accesses — `self.<attr>` for Python
(via AST walk), field-name-matched `MemberReference` nodes for Java (via
`javalang`, which also naturally captures `this.<field>` since that's
represented as a `This` node wrapping a `MemberReference`). Then compare
every method pair's field-access sets.

**Interpretation.** `LCOM1 = 0` → methods are, on balance, well connected
through shared state (good cohesion). Larger values → the class's methods
don't share state — a "God class" / multiple-responsibility smell.

**Returned as `null` (not a fabricated number) when:**
- the class has fewer than 2 methods (no pairs exist to compare), or
- the class has no instance fields at all (nothing for methods to share).

Module-level `cohesion` = average LCOM1 across the module's classes
(excluding `null` classes); `null` if the module has no classes or none
were scorable.

**Known limitation (documented, not hidden).** LCOM1 only looks at direct
field access, per the original 1991 definition — it does not credit a
method that calls another method of the same class (a common alternate
way cohesion manifests) with sharing state. A well-known consequence: a
constructor that initializes every field creates a "shared access" link
between every method pair (since every method's access set overlaps with
the constructor's), which can mask real cohesion problems elsewhere in
the class. `tests/test_cohesion.py` documents and demonstrates this
directly — a genuinely low-cohesion class only scores above zero once its
methods are tested without a shared all-fields constructor in the mix.
Later LCOM variants (LCOM2-5) address this; they are out of scope here.

---

## 7. Depth of Inheritance (DIT)

**Definition (Chidamber & Kemerer).** For class `C`, `DIT(C)` = the number
of ancestor classes from `C` up to its root base class.

**Calculation method** (`metrics/inheritance.py`): because a subclass and
its base are very often defined in *different files*, DIT cannot be
computed per file in isolation. `build_class_registry` first scans every
parsed module in the whole uploaded project and builds one map of
`class_name -> base_class_name`. `calculate_class_dit` then walks that map
upward from a given class, counting depth, with:

- **Cycle safety:** a set of visited class names is tracked while walking;
  if a name is revisited (e.g. a malformed `A extends B; B extends A`),
  the walk stops and returns the depth reached so far — DIT calculation
  can never infinite-loop, even on adversarial input.
- **Unresolved external bases:** if a class extends something not defined
  anywhere in the uploaded project (a JDK/stdlib/third-party class we
  never parsed), that still counts as one level of depth, then the walk
  stops — we know inheritance exists, we just can't see further up. This
  makes the reported DIT a lower bound in that case, not a wrong value.

**Single inheritance only.** Only the *first* (`extends`) base is tracked.
This is exact for Java (single class inheritance). For Python (which
allows multiple inheritance), this is a documented simplification — a
class inheriting meaningfully from a second or third base will have its
DIT under-measured along that second lineage.

Module-level `depth_of_inheritance` = average DIT across the module's
classes; `null` if the module has no classes.

**Verification.** `tests/test_inheritance.py` reproduces the exact
`Base -> Service -> PaymentService` example from the project specification
and asserts `DIT(PaymentService) == 2`, plus a cyclic-inheritance test that
asserts the calculation terminates.

---

## 8. Code Duplication

**Method: normalized-line, sliding-window block hashing**, computed
**project-wide** (duplication is inherently a cross-file phenomenon — the
same helper copy-pasted into two files is exactly the case this metric
must catch, so it is never computed one file at a time).

**Algorithm** (`metrics/duplication.py`):
1. For every file, take its non-blank lines and normalize each one
   (strip leading/trailing whitespace, collapse internal whitespace runs
   to a single space) — this makes comparison indentation- and
   formatting-insensitive.
2. Slide a window of `BLOCK_SIZE = 6` consecutive normalized lines over
   every file and hash each window.
3. Any window hash occurring **2 or more times** across the whole
   project — in the same file or a different one — marks every line in
   every occurrence of that window as duplicated.
4. `duplicated_lines` (per file, and project total) = count of distinct
   lines marked duplicated. `duplication_percentage = duplicated_lines /
   total_lines_considered * 100`.

**Why `BLOCK_SIZE = 6`.** Too small (2-3 lines) and short, coincidentally
similar snippets (e.g. `if x is None: return None`) get flagged as
"duplication." Too large and only near-identical large functions get
caught. Six lines is a common default in academic and industrial
line-based clone-detection tooling for exactly this reason, and is
configurable (the constant lives at the top of `duplication.py`).

**Limitations.** This is line/token-based clone detection (catches
"Type-1/Type-2" clones — identical or renamed-variable duplicates that
still line up textually) — it is **not** AST-based, so structurally
identical code with statements reordered, or logic re-expressed
differently, will not be caught. This is a standard, defensible
simplification for a student project and is documented rather than
silently glossed over.

**Verification.** `tests/test_duplication.py` includes a deliberately
duplicated block across two files (detected), a within-file duplicate
(detected), an indentation-only difference (still detected — confirms
normalization works), and two genuinely distinct files (0% duplication).
The sample project itself (`sample_projects/demo_python/auth.py` and
`user.py`) contains a real duplicated `is_valid_username` method to
demonstrate this end to end.

---

## Summary table

| Metric | Method | Library used | Exact / Approximation |
|---|---|---|---|
| LOC | Grammar-aware line classification | radon (Python) / custom (Java) | Exact per documented definition |
| Classes / Functions | AST traversal | `ast` (Python) / `javalang` (Java) | Exact |
| Cyclomatic Complexity | McCabe decision-point counting | radon (Python) / custom AST walk (Java) | Exact per McCabe formula |
| Maintainability Index | Oman/Coleman formula (Halstead + CC + LOC + comments) | radon (Python) / custom Halstead + shared formula (Java) | Exact formula; Java Halstead uses a standard token-classification simplification |
| Coupling | Import / extends-implements reference counting | Custom | Documented approximation, not full CBO |
| Cohesion | LCOM1 | Custom | Exact per LCOM1 definition; known LCOM1 constructor-masking limitation applies |
| Depth of Inheritance | Project-wide `extends` chain walk | Custom | Exact for single inheritance; first-base-only simplification for Python multiple inheritance |
| Code Duplication | Normalized 6-line sliding-window hashing | Custom | Type-1/Type-2 clone detection only, not AST-based |

C/C++ and JavaScript/TypeScript use custom, dependency-free lexer-based
front ends feeding the same calculators; their per-metric behaviour is
documented in sections 9 and 10.

Every metric that cannot be reliably computed for a given file/class
returns `null` rather than a fabricated value (see Cohesion and DIT above
for the specific conditions).

---

## 9. C and C++ support

Supported languages: Python, Java, **C**, **C++**, JavaScript and TypeScript
(JavaScript/TypeScript are described in section 10).

C/C++ flow through the same pipeline and the same scoring/risk system as
Python and Java — no separate formulas, weights, thresholds or dashboard.
Only the *front end* differs: there is no real C/C++ parser in this
project's dependency set, so `analyzer/cfamily.py` implements a small,
dependency-free **static approximation** in two layers:

1. **Lexer pass** — separates code from comments, string/char literals
   (including C++ raw strings and `1'000` digit separators) and
   preprocessor directives. Every later step works on these cleaned views,
   so comment-like text in strings and braces in literals never affect
   structure or metrics.
2. **Structure scanner** — tracks `namespace` / `class` / `struct` /
   `union` / `extern "C"` scopes and recognizes function definitions
   (free, member, out-of-line `Foo::bar`, constructors, operators,
   templates), classes with base lists, fields, `#include`s and calls.

**Detection.** `.c` -> C; `.cpp .cc .cxx .hpp` -> C++; `.h` is decided per
file by content: C++ if it uses C++-only syntax (`class`, `namespace`,
`template<`, `::`, access labels, `virtual`, `nullptr`, extension-less
standard includes such as `<vector>`, ...), otherwise C. An
`#ifdef __cplusplus extern "C"` guard alone keeps a header C.

| Metric | C / C++ behaviour |
| --- | --- |
| LOC | Lexer-based. A line with only comment text (or inside a block comment, blank lines included) is a comment line; code with a trailing comment is code; preprocessor directives are code. `total = code + comments + blank` always holds. |
| Functions | Number of function **definitions** (free + member, incl. constructors). Prototypes in headers are declarations and are not counted. |
| Classes | `class`, `struct`, `union` definitions (a C `struct` counts as a class). |
| Cyclomatic complexity | Same convention (`1 + decision points` per function): `if`, `for`, `while` (incl. the `while` of do-while), `case`, `catch`, `&&`, `\|\|`, `?:`. `else`, `default` and `do` add nothing. A file with no function bodies reports 0 and a `null` complexity score. |
| Maintainability | Identical `_mi_compute` formula as Java; Halstead tokens come from a regex tokenizer (keywords/operators/punctuation = operators; identifiers and literals = operands). |
| Coupling | Distinct `#include` targets ∪ base classes. A file's own header (`foo.c` -> `foo.h`) is not counted. Function calls are deliberately **not** counted: without a symbol table they cannot be separated from library calls and would double-count the includes. |
| Cohesion | LCOM1 (same definition) per C++ class, using the member functions that have a body in the same file and the fields their bodies mention. If fewer than two methods have bodies in the file (e.g. a header whose bodies are in the `.cpp`), or a class has no fields/methods (every C struct), it is `null`, never a fabricated value. |
| Inheritance (DIT) | C++: first base class, project-wide registry, same algorithm as Java (extra bases are tracked as `interface_names` and counted for coupling). C: no inheritance, so the neutral default `null` is kept. |
| Duplication | Unchanged shared algorithm. |

**Known limitations.**

- Macros are not expanded and their bodies are ignored (blanked with the
  rest of the directive); `#if/#else` branches are not evaluated, so a file
  whose branches have different brace balance is reported as a parse error
  (`Unbalanced braces`) rather than guessed at.
- K&R-style definitions (`int f(a, b) int a; int b; { ... }`) are not
  recognized.
- `&&` is counted as a logical operator, so an rvalue-reference *local*
  such as `auto&& x = ...` slightly over-counts complexity.
- Method bodies defined in a different file than their class declaration
  are not merged back to the class (cohesion is `null` for such headers).
- Templates are not instantiated; template parameters are ignored.
- Lambdas and nested local functions are counted inside their enclosing
  function, not as separate functions.
- Cohesion matches field names as whole words, so a local variable that
  shadows a field name is over-counted as field access.

---

## 10. JavaScript and TypeScript support

Supported languages: Python, Java, C, C++, **JavaScript**, **TypeScript**.

JavaScript and TypeScript flow through the same pipeline and the same
scoring/risk system as every other language — no separate formulas,
weights, thresholds, API routes or dashboard. As with C/C++, only the
*front end* differs. The project's dependency set has no JavaScript/
TypeScript parser, and pulling in a full compiler would be out of
proportion for a metrics tool, so `analyzer/jsfamily.py` implements a
dependency-free **static approximation** in three layers:

1. **Lexer pass.** A character-level pass that separates real code from
   everything that merely *looks* like code. It handles `//` and `/* */`
   comments (and a leading `#!` hashbang line), strings with escapes,
   **template literals** (the literal text is blanked, but each
   `${ ... }` expression is kept as code and may itself contain further
   templates), **regular-expression literals** (told apart from division
   by the previous significant token), and **JSX** (tag names, attributes
   and text are blanked; each `{ ... }` expression container is kept as
   code and the whole element behaves as a single operand). It produces a
   comment-free view and a fully cleaned view of the file, both exactly as
   long as the original. Consequently a brace, a `//`, an apostrophe or
   the word `if` inside a string, template, regex or JSX text can never be
   mistaken for structure or for a decision point.
2. **Tokenizer and bracket matcher.** The cleaned view is cut into
   identifier / number / string / punctuator tokens and every `()`, `[]`
   and `{}` is paired. Unbalanced or mismatched brackets, or an
   unterminated `${...}` / JSX expression, are reported as a file-level
   parse error (the same treatment as a Python or Java syntax error); the
   analyzer never crashes on malformed input.
3. **Structure scanner.** Walks the tokens and recognizes functions,
   classes, interfaces, imports and exports (details below).

### 10.1 Detection and scanning

| Rule | Behaviour |
| --- | --- |
| `.js`, `.jsx` | JavaScript |
| `.ts`, `.tsx` | TypeScript |
| `.mjs`, `.cjs` | **Not scanned** (not currently supported) |
| `*.min.js` | Skipped: minified output is generated code, not authored source |
| `.ts` containing a NUL byte in its first 4 KB | Skipped: `.ts` is also the MPEG transport-stream video extension, and text source never contains NUL |
| Ignored directories | The existing list (`node_modules`, `dist`, `build`, `bin`, `out`, `target`, hidden directories, ...) applies as for every language |
| JSX recognition | Enabled for `.js`, `.jsx` and `.tsx`. Disabled for plain `.ts`, where `<T>x` is a type assertion, not markup |
| `.d.ts` | Analyzed as TypeScript. A declaration file is an ambient context that cannot contain executable code, so every `=>` in one is treated as part of a *type*, never as an arrow function; `declare function` signatures are not function definitions, while declared classes and interfaces are counted |

### 10.2 What the scanner recognizes

- **Functions:** function declarations (including `async` and generators),
  function expressions, arrow functions (block- and expression-bodied),
  object-literal methods, class methods, getters and setters, `static` and
  private `#name` members, constructors, and arrow functions stored in
  class fields. Anonymous functions are named from what they are assigned
  to (`const f = () => ...` is `f`, `exports.handler = ...` is `handler`,
  `{ go: () => ... }` is `go`); callbacks passed as arguments are
  functions named `<anonymous>`.
- **Not function definitions:** overload signatures, abstract methods,
  `declare function`, and function *types* (`(x: number) => void`,
  `new () => T`, including those nested in generic type arguments).
- **Classes:** `class` declarations and expressions with `extends`, TS
  `implements`, declared fields, TS parameter properties
  (`constructor(private x: T)`), and `static { }` blocks and callbacks
  inside class-field initializers (attributed to the class).
- **TypeScript declarations:** `interface` (with `extends`, possibly
  several), `type` aliases, `enum` and `namespace`/`module`. Interfaces
  are modelled as classes; type aliases, enums and namespaces are
  recognized (so their contents are skipped correctly) but are not counted
  as classes.
- **Imports, exports, calls:** `import ... from`, side-effect `import 'x'`,
  `require('x')`, dynamic `import('x')`, `export ... from 'x'`,
  `import x = require('x')`, `export` declarations and lists,
  `module.exports` / `exports.x`; and the calls made by each function.

The result is mapped onto the existing structures: a class or interface
becomes a `ParsedClass` (`base_name` is the first `extends`; any further
`extends` of an interface and any TS `implements` go to
`interface_names`); free functions, arrow functions and callbacks become
`functions`; class methods are listed on their class; imports and
requires become `imports`. As in Java and C++, a **constructor is a
function definition but not a method name**.

### 10.3 Per-metric behaviour

| Metric | JavaScript / TypeScript behaviour |
| --- | --- |
| LOC | Lexer-based, with the same conventions as C/C++. A line holding only comment text, or lying inside a block comment (blank lines included), is a comment line; code with a trailing comment is code. Lines inside a multi-line template literal are **code** even if they contain `//` or `/* */`; JSX text lines are code; a hashbang is a comment. `total = code + comments + blank` always holds. |
| Functions | Number of function **definitions** (free, arrow, callbacks, methods, constructors). Only *outermost* functions are counted: a nested function or callback is part of its enclosing function. |
| Classes | `class` declarations/expressions plus TypeScript interfaces. Type aliases, enums and namespaces are not counted. |
| Cyclomatic complexity | Same convention as every language: `CC = 1 + decision points` per function (section 3). See 10.4. |
| Maintainability Index | Identical formula as the other languages (section 4). The Halstead counts come from the cleaned token stream: keywords and punctuators are *operators*; identifiers, numbers and string/template literals are *operands*. Comments, regex bodies and JSX text are excluded. |
| Coupling | Distinct module specifiers (`import`, `require`, dynamic `import()`, `export ... from`) unioned with inheritance bases (`extends`, TS `implements`, interface `extends`). A base class that is itself an imported binding is **not counted a second time**, since its import already represents the dependency. Function calls are deliberately **not** counted: without type information they cannot be told apart from local or library calls. |
| Cohesion | LCOM1, the same definition as section 6, computed per class from the methods that have a body and the class's own fields they touch through `this.<name>` (including `this.#private`). Fields are declared fields, TS parameter properties, and names assigned or read as `this.x` in the class (plain JavaScript declares fields by assigning them in the constructor). The constructor is not a method; class-field arrows are; a getter and setter of the same name are merged. See 10.5 for the `null` cases. |
| Inheritance (DIT) | First `extends` base of a class (or of an interface), through the same project-wide class registry and the same algorithm as section 7: an unresolved external base counts one level and the walk stops. `implements` is recorded for coupling but does not affect DIT. |
| Duplication | The shared algorithm of section 8, unchanged. |

### 10.4 Complexity details

Counted over the cleaned token stream, so keywords inside strings,
templates, comments, regex bodies and JSX text are never seen:

- **+1:** `if`, `for` (including `for...of`, `for...in`, `for await`),
  `while`, `case`, `catch`, `&&`, `||`, `??`, and the ternary `?:`. The
  `while` that closes a `do...while` is the one counted decision.
  Short-circuit operators inside JSX expression containers
  (`{ok && <b/>}`) count; `??` is counted as a short-circuit operator like
  `||`.
- **+0:** `else`, `default`, `do`, `try`, `finally`, optional chaining `?.`,
  and the TypeScript optional-parameter / optional-property marker `?`
  (`a?: T`, `(a?) =>`).
- **`catch` counts only when it is a catch clause**, i.e. when the `}`
  right before it closes a block opened by `try`. A *method* named `catch`
  (as in thenable classes) and `promise.catch(...)` are not decisions.
- Property names such as `obj.if` or `{ for: 1 }` are not decisions.
- Parameter default values belong to the signature, not the body.
- Code outside any function (module-level statements) is not counted. A
  file with no function definitions reports complexity 0 and a `null`
  complexity score, never a fabricated value.

### 10.5 When cohesion is `null`

Cohesion is `null` (never an invented number) when a class has fewer than
two methods with a body in the file, when it has no fields, and for every
interface (it has no bodies at all). Abstract methods and overload
signatures have no body and are left out of the calculation rather than
being counted as touching nothing. A module's cohesion is the average over
its calculable classes, and `null` if there are none.

### 10.6 Validation

The scanner was validated against real-world source rather than only
hand-written examples. During development the TypeScript compiler's own
parser was used offline as an independent *oracle* (it is not a runtime
dependency): for each file it gives the number of outermost
function-likes and classes/interfaces and the complexity under this
project's convention, which were compared with this analyzer's output.

| Check | Result |
| --- | --- |
| Source files collected (npm's own code base and globally installed packages) | 20,249 `.js/.jsx/.ts/.tsx` files |
| Analyzed after the normal scanner ignores and `.min.js` skips | 15,018 files (JavaScript and TypeScript) |
| Scanner / parser errors | 0 |
| Metric invariant violations (LOC adds up, MI in 0-100, cohesion >= 0, ...) | 0 |
| Health score and risk calculation | succeeded on every corpus |
| Functions vs. oracle | 15,003 / 15,015 files agree |
| Classes and interfaces vs. oracle | 15,015 / 15,015 files agree |
| Complexity vs. oracle | 15,003 / 15,015 files agree |
| Excluded from the oracle comparison | 3 files in which the compiler itself reported syntax errors |

All 12 remaining disagreements are the documented ternary-arrow
limitation below. Robustness against malformed input was checked by
truncating and mutating real files and adding pathological cases
(561 inputs): none crashed, 387 produced the expected parse error and 174
parsed successfully.

### 10.7 Known limitations

1. **Ternary-arrow over-merge.** In `cond ? (x) => a : (y) => b` the first
   arrow's expression body runs on into the alternate branch, so the two
   arrows are reported as one function. Their decision points are kept;
   only the second function's baseline `+1` is lost. This is the only
   remaining disagreement with the oracle.
2. **Nested functions and callbacks** are attributed to their enclosing
   function rather than reported separately (a deliberate simplification,
   shared with C/C++ lambdas).
3. **A base class defined in the same file** still counts toward coupling;
   only *imported* bases are excluded from the second count.
4. **`this` destructuring is not detected** by cohesion
   (`const { x } = this`); only `this.x` access is. Because cohesion
   matches names textually, a local variable cannot be told apart from a
   field of the same name.
5. **Decorators** are skipped, not analysed.
6. **Unusual or unsupported syntax** can cause a whole-file parse error,
   as can any unbalanced bracket.
7. **TypeScript conditional types.** A `?:` that is a conditional type
   (`T extends U ? X : Y`) written *inside a function body* is counted as
   a ternary and adds one extra point.
8. **Methods named like control-flow keywords.** A method named `if`,
   `for` or `while` is still counted as that keyword (extremely rare;
   `catch` is handled, see 10.4).
9. **`.mjs` and `.cjs` files are not supported** and are not scanned.
10. **Very large generated files parse slowly** (on the order of half a
    second per megabyte), and minified bundles whose names do not end in
    `.min.js` are not recognized as minified.
