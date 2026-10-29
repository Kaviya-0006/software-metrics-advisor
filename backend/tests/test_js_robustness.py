"""
Regression tests found by running the JS/TS pipeline over real-world source
(npm's own code base, compared against the TypeScript compiler's AST), plus
explicit checks that the *documented limitations* still behave as documented.
"""

from pathlib import Path

from app.analyzer.engine import analyze_project
from app.analyzer.jsfamily import parse_js_family_source


def js(src: str):
    return parse_js_family_source(src, "javascript", True)


def ts(src: str):
    return parse_js_family_source(src, "typescript", False)


def _only_module(tmp_path: Path, name: str, code: str):
    path = tmp_path / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(code)
    analysis = analyze_project(tmp_path)
    assert len(analysis.modules) == 1
    return analysis.modules[0]


# ----------------------------------------- fix A: arrows in parameter defaults


def test_arrow_in_destructured_parameter_default_is_not_a_separate_function():
    # npm: treeverse/lib/breadth.js
    tree = js("const breadth = ({ visit, filter = () => true, tree }) => {\n  return filter(tree);\n};\n")
    assert [f.name for f in tree.functions] == ["breadth"]


def test_arrow_in_plain_parameter_default_is_not_a_separate_function():
    # npm: @npmcli/redact/lib/deep-map.js
    tree = js("const deepMap = (input, handler = v => v, seen = new Set([input])) => handler(input);\n")
    assert [f.name for f in tree.functions] == ["deepMap"]


def test_parameter_default_arrow_adds_no_complexity_to_the_enclosing_function(tmp_path):
    module = _only_module(tmp_path, "m.js", "const f = (a, g = x => x ? 1 : 2) => a;\n")
    assert module.functions == 1
    assert module.complexity_total == 1  # defaults are signature, not body


def test_following_functions_survive_the_parameter_default_cleanup():
    src = (
        "const a = (x = () => 1) => x();\n"
        "const b = (y) => y;\n"
        "function c(z = () => 2) { return z(); }\n"
    )
    assert [f.name for f in js(src).functions] == ["a", "b", "c"]


# -------------------------------- fix B: callbacks in class fields / static blocks


def test_callbacks_inside_class_field_initializers_are_functions_of_the_class():
    # npm: lib/commands/doctor.js and lib/utils/display.js
    src = (
        "class D {\n"
        "  static usage = [items.flatMap(s => s.groups).filter((v, i, self) => self.indexOf(v) === i && v !== 'x')];\n"
        "  #handler = wrap((level) => {\n"
        "    switch (level) { case 1: return 1; case 2: return 2; }\n"
        "  });\n"
        "}\n"
    )
    tree = js(src)
    assert len(tree.functions) == 3
    assert {f.owner_class for f in tree.functions} == {"D"}


def test_class_field_callbacks_contribute_their_complexity(tmp_path):
    code = (
        "class D {\n"
        "  static usage = [items.flatMap(s => s.groups).filter((v, i, self) => self.indexOf(v) === i && v !== 'x')];\n"
        "  #handler = wrap((level) => {\n"
        "    switch (level) { case 1: return 1; case 2: return 2; }\n"
        "  });\n"
        "}\n"
    )
    module = _only_module(tmp_path, "d.js", code)
    assert module.functions == 3
    assert module.complexity_total == 1 + 2 + 3  # s=>…, (v,i,self)=>… with &&, handler with 2 cases


def test_class_field_that_is_itself_an_arrow_is_still_a_method_not_a_callback():
    tree = js("class H { x = 0; inc = () => { this.x++; }; }\n")
    h = next(c for c in tree.classes if c.name == "H")
    assert h.methods == ["inc"] and h.fields == ["x"]
    assert len(tree.functions) == 1


def test_callbacks_in_static_blocks_are_counted(tmp_path):
    module = _only_module(tmp_path, "s.js", "class S {\n  static {\n    init(() => { if (a) b(); });\n  }\n}\n")
    assert module.functions == 1 and module.complexity_total == 2


def test_this_access_inside_a_field_callback_still_declares_the_field():
    tree = js("class C { h = wrap(() => this.count); }\n")
    assert "count" in next(c for c in tree.classes if c.name == "C").fields


# ----------------------------------------- documented limitations still hold


def test_limitation_ternary_of_two_arrows_is_one_function():
    # Documented approximation: the first arrow's expression body runs on into
    # the `: alternate` branch. (npm: nopt/lib/debug.js, semver/internal/debug.js)
    tree = js("module.exports = debug ? (...a) => console.error(...a) : () => {};\n")
    assert len(tree.functions) == 1


def test_limitation_nested_functions_are_attributed_to_the_enclosing_function(tmp_path):
    code = "function outer(x) {\n  function inner(y) { if (y) return 1; return 0; }\n  return inner(x);\n}\n"
    module = _only_module(tmp_path, "n.js", code)
    assert module.functions == 1 and module.complexity_total == 2


def test_limitation_local_base_class_still_counts_toward_coupling(tmp_path):
    module = _only_module(tmp_path, "l.js", "class A {}\nclass B extends A {}\n")
    assert module.coupling == 1


def test_limitation_this_destructuring_is_not_seen_by_cohesion(tmp_path):
    code = (
        "class D {\n"
        "  constructor() { this.b = 1; }\n"
        "  m1() { const { b } = this; return b; }\n"
        "  m2() { return this.b; }\n"
        "}\n"
    )
    # If destructuring were detected both methods would share `b` (0.0).
    assert _only_module(tmp_path, "d.js", code).cohesion == 1.0


def test_limitation_decorators_are_skipped_but_do_not_break_parsing(tmp_path):
    code = (
        "@Component({ selector: 'x' })\n"
        "class A {\n"
        "  @Input() name: string;\n"
        "  @HostListener('click') onClick() { if (this.name) { go(); } }\n"
        "}\n"
    )
    module = _only_module(tmp_path, "a.ts", code)
    assert module.parse_error is None
    assert module.classes == 1 and module.functions == 1 and module.complexity_total == 2


def test_limitation_unbalanced_brackets_are_a_parse_error(tmp_path):
    assert _only_module(tmp_path, "bad.js", "function f() { return (1;\n").parse_error is not None


# ------------------- fix C: the *type* `void` must end a type (no semicolons)


def test_void_type_without_semicolon_does_not_swallow_the_next_declaration():
    # real .d.ts files: fastq, parse-entities, undici-types, jszip
    src = (
        "type Handler = (err: Error, task: number) => void\n"
        "interface Queue { size: number }\n"
        "declare function install (): void\n"
        "export {}\n"
    )
    tree = ts(src)
    assert [c.name for c in tree.classes] == ["Queue"]
    assert tree.type_aliases == ["Handler"]
    assert tree.functions == []  # `declare function` has no body: not a definition


def test_void_typed_abstract_member_without_semicolon_keeps_the_next_method():
    src = "abstract class A {\n  abstract run(): void\n  go() { if (a) { b(); } }\n}\n"
    tree = ts(src)
    a = next(c for c in tree.classes if c.name == "A")
    assert a.methods == ["run", "go"]
    assert [f.name for f in tree.functions] == ["go"]


# ------- fix D: a newline inside a template literal never ends an arrow body


def test_multiline_template_literal_arrow_body_keeps_inner_callbacks_inside():
    # real code: pdf-lib src/core/embedders/CMap.ts
    src = (
        "const f = (items) => `\n"
        "<ul>\n"
        "${items.map(i => `<li>${i}</li>`).join('\\n')}\n"
        "</ul>\n"
        "`;\n"
        "const g = (a) => a;\n"
    )
    assert [f.name for f in js(src).functions] == ["f", "g"]


def test_ternary_spanning_lines_inside_a_template_counts_for_the_enclosing_arrow(tmp_path):
    code = "const t = (a) => `\n  ${a ? 'x'\n      : 'y'}\n`;\nconst u = () => 1;\n"
    module = _only_module(tmp_path, "t.js", code)
    assert module.functions == 2 and module.complexity_total == 2 + 1


def test_statement_after_a_template_literal_still_ends_the_arrow_without_semicolons():
    src = "const h = (a) => `x`\nconst i = (b) => b\n"
    assert [f.name for f in js(src).functions] == ["h", "i"]


# --------- fix E: function *types* are not functions (`.d.ts`, `new (...) => T`)


_DECLARATION_TYPES = (
    "export const CallableInstance: new <P extends unknown[], R>(property: string | symbol) => (...parameters: P) => R;\n"
    "export const is: ((<C extends string>(node: unknown, test: C) => node is Node) & ((node?: null) => false));\n"
    "export namespace codes {\n"
    "  let ERR_A: new (...parameters: Array<any>) => Error;\n"
    "}\n"
)


def test_declaration_file_arrows_are_types_never_functions(tmp_path):
    # real .d.ts files: unified/lib/callable-instance.d.ts, unist-util-is/lib/index.d.ts,
    # import-meta-resolve/lib/errors.d.ts
    module = _only_module(tmp_path, "types.d.ts", _DECLARATION_TYPES)
    assert module.functions == 0
    assert module.complexity_total == 0
    assert module.parse_error is None


def test_declaration_flag_is_only_set_for_dot_d_dot_ts(tmp_path):
    # the same text in a normal .ts file is not an ambient context, so the
    # scanner must not silently drop genuine arrow functions there
    module = _only_module(tmp_path, "real.ts", "export const f = (a: number) => a > 0 ? 1 : 2;\n")
    assert module.functions == 1 and module.complexity_total == 2


def test_constructor_and_conditional_types_in_plain_ts_are_not_functions():
    src = (
        "let make: new (a: number) => Foo;\n"
        "let pick: Box extends (x: any) => infer R ? R : never;\n"
        "function g(c: new () => T) { return c; }\n"
    )
    assert [f.name for f in ts(src).functions] == ["g"]


# --------- fixes F-I: function *types* inside TypeScript type syntax (puppeteer-core)


def test_parenthesised_function_type_as_return_type_does_not_hide_the_real_arrow(tmp_path):
    # real code: puppeteer-core src/common/Debug.ts (complexity was 1 instead of 7)
    code = (
        "export const debug = (prefix: string): ((...args: unknown[]) => void) => {\n"
        "  if (isNode) { return () => {}; }\n"
        "  return () => {};\n"
        "};\n"
    )
    module = _only_module(tmp_path, "debug.ts", code)
    assert module.functions == 1  # the returned arrows are part of debug's body
    assert module.complexity_total == 2


def test_ternary_arrow_after_a_call_is_not_mistaken_for_a_return_type():
    # plain JavaScript: `c ? f(x) : (y) => y` must still find the arrow
    assert len(js("const z = c ? f(x) : (y) => y;\n").functions) == 1


def test_function_types_in_generic_type_arguments_are_not_functions():
    # real code: puppeteer-core src/util/Function.ts
    src = (
        "const created = new Map<string, (...args: unknown[]) => unknown>();\n"
        "const f = () => 1;\n"
        "const [state, setState] = useState<() => void>(() => { if (a) { b(); } });\n"
    )
    assert [f.name for f in ts(src).functions] == ["f", "<anonymous>"]


def test_comparison_operators_are_not_mistaken_for_type_arguments():
    tree = ts("const ok = a < b && c > (d) ;\nconst f = () => 1;\n")
    assert [f.name for f in tree.functions] == ["f"]


def test_arrow_type_after_as_is_not_a_function():
    # real code: puppeteer-core src/cdp/ExecutionContext.ts
    tree = ts("const h = wrap((a) => a) as (...args: unknown[]) => unknown;\n")
    assert len(tree.functions) == 1  # only the callback


def test_object_literal_entries_with_generic_return_types_are_not_split_at_the_comma():
    # real code: puppeteer-core src/api/locators/locators.ts
    src = (
        "class L {\n"
        "  protected operators = {\n"
        "    conditions: (c: Array<A>, s?: Signal): OperatorFunction<Handle<T>, Handle<T>> => { return 1; },\n"
        "    race: <T>(s?: Signal): OperatorFunction<T, T> => { return 2; },\n"
        "  };\n"
        "}\n"
    )
    tree = ts(src)
    assert [f.name for f in tree.functions] == ["conditions", "race"]
    assert {f.owner_class for f in tree.functions} == {"L"}


# ------------- fix J: a *method named* `catch` is not a catch clause (zod, thenables)


def test_method_named_catch_is_not_a_decision_point(tmp_path):
    # real code: zod/lib/index.umd.js (class ZodType { ... catch(def) {...} })
    code = (
        "function build() {\n"
        "  class P {\n"
        "    then(f) { return f; }\n"
        "    catch(f) { return f; }\n"
        "  }\n"
        "  class Q { catch() { return 1; } }\n"
        "  const o = { catch(e) { return e; } };\n"
        "  try { run(); } catch (e) { log(e); }\n"
        "}\n"
    )
    module = _only_module(tmp_path, "thenable.js", code)
    assert module.functions == 1
    assert module.complexity_total == 2  # only the real try/catch counts


def test_real_catch_clauses_are_still_counted_in_every_form(tmp_path):
    code = (
        "function f() {\n"
        "  try { a(); } catch { b(); }\n"
        "  try { try { c(); } catch (e) { d(); } } catch (g) { h(); }\n"
        "  try { i(); } catch ({ message }) { j(message); } finally { k(); }\n"
        "}\n"
    )
    assert _only_module(tmp_path, "catches.js", code).complexity_total == 1 + 4
