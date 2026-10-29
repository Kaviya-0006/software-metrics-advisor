"""
Parser-layer tests for the dependency-free JavaScript/TypeScript scanner
(app/analyzer/jsfamily.py). Pipeline integration (metrics, scoring, upload)
is tested separately once it is wired in.
"""

import pytest

from app.analyzer.jsfamily import JsFamilyParseError, lex_source, parse_js_family_source


def js(src: str, jsx: bool = True):
    return parse_js_family_source(src, "javascript", jsx)


def ts(src: str, jsx: bool = False):
    return parse_js_family_source(src, "typescript", jsx)


def fn_names(tree):
    return [(f.name, f.owner_class) for f in tree.functions]


def cls(tree, name):
    return next(c for c in tree.classes if c.name == name)


# ----------------------------------------------------------------------- lexer


def test_lexer_views_keep_original_length_and_blank_strings_and_comments():
    src = 'const s = "{ // }"; // trailing { comment\n/* block { */ const t = 1;\n'
    lexed = lex_source(src)
    assert len(lexed.clean) == len(lexed.no_comments) == len(src)
    assert "{" not in lexed.clean and "//" not in lexed.clean and "block" not in lexed.clean
    assert '"{ // }"' in lexed.no_comments  # strings survive in the comment-free view
    assert lexed.comment_lines == {0, 1}


def test_lexer_template_literal_text_is_blank_but_expressions_are_code():
    lexed = lex_source("const t = `a { b ${ x ? 1 : 2 } c }`;\n")
    assert "x ? 1 : 2" in lexed.clean
    assert "a { b" not in lexed.clean


def test_lexer_regex_literal_vs_division():
    lexed = lex_source("const r = /[{}]\\/\\//g; const d = a / b / c;\n")
    assert "{" not in lexed.clean and "}" not in lexed.clean
    assert "a / b / c" in lexed.clean


# ---------------------------------------------------------------- JS: functions


def test_js_simple_function():
    tree = js("function add(a, b) {\n  return a + b;\n}\n")
    assert fn_names(tree) == [("add", None)]


def test_js_multiple_functions_and_function_expression():
    tree = js("function a() {}\nfunction b() {}\nconst c = function named() { return 1; };\n")
    assert [f.name for f in tree.functions] == ["a", "b", "named"]


def test_js_arrow_functions_are_named_from_their_target():
    src = (
        "const a = x => x * 2;\n"
        "const b = async (x, y) => { if (x) return y; };\n"
        "const fn = (a, b) => a && b;\n"
        "exports.handler = async (e) => 1;\n"
        "const o = { run() { return 1; }, go: () => 2 };\n"
    )
    assert [f.name for f in js(src).functions] == ["a", "b", "fn", "handler", "run", "go"]


def test_js_brace_inside_string_in_arrow_does_not_confuse_scanner():
    src = (
        "const x = () => {\n"
        '    return "{ not a real structural brace }";\n'
        "};\n"
        "function after() { return 1; }\n"
    )
    assert [f.name for f in js(src).functions] == ["x", "after"]


def test_js_difficult_strings_templates_and_regex_together():
    src = (
        "// { comment\n"
        "/* } block */\n"
        "const re = /[{}]\\/\\//g;\n"
        'const url = "http://x.com/*not a comment*/";\n'
        "const t = `a ${ cond ? `n ${ {a: 1}.a }` : \"}\" } b }`;\n"
        "function f(a) { return a / 2 / 3; }\n"
    )
    tree = js(src)
    assert [f.name for f in tree.functions] == ["f"]


def test_js_function_calls_are_collected_without_keywords():
    tree = js("function f(x) { if (x) { return g(x) + h.k(1); } return new Foo(); }\n")
    assert tree.functions[0].calls == ["Foo", "g", "h.k"]


# ------------------------------------------------------------------ JS: classes


def test_js_class_methods_constructor_and_extends():
    src = (
        "class User extends Base {\n"
        "  static count = 0;\n"
        "  #secret = 1;\n"
        "  handle = () => { this.x = 1; };\n"
        "  constructor(name) { super(); this.name = name; }\n"
        "  get label() { return this.name; }\n"
        "  set label(v) { this.name = v; }\n"
        "  async load() { await fetch(this.url); }\n"
        "  static make() { return new User(); }\n"
        "  *gen() { yield 1; }\n"
        "}\n"
    )
    tree = js(src)
    user = cls(tree, "User")
    assert user.bases == ["Base"]
    assert user.methods == ["handle", "label", "load", "make", "gen"]  # constructor is not a method
    assert {"count", "#secret", "x", "name", "url"} <= set(user.fields)
    ctor = [f for f in tree.functions if f.name == "constructor"]
    assert len(ctor) == 1 and ctor[0].owner_class == "User"  # but it is a function definition
    assert tree.method_bodies["User"]["label"].count("this.name") == 2  # getter + setter merged


def test_js_anonymous_class_default_export_keeps_extends():
    tree = js("export default class extends User {}\n")
    assert cls(tree, "<anonymous>").bases == ["User"]
    assert tree.exports == ["default"]  # `extends` must not be mistaken for a name


# ----------------------------------------------------------- JS: imports/exports


def test_js_imports_requires_and_bindings():
    src = (
        "import React, { useState as us } from 'react';\n"
        "import * as ns from \"./ns\";\n"
        "import './side.css';\n"
        "const fs = require('fs');\n"
        "const { a, b } = require(\"./ab\");\n"
        "const lazy = () => import('./lazy');\n"
        "const x = Array.from('abc');\n"
    )
    tree = js(src)
    assert tree.imports == ["./ab", "./lazy", "./ns", "./side.css", "fs", "react"]
    assert {"React", "us", "ns", "fs", "a", "b"} <= tree.import_bindings


def test_js_exports():
    src = (
        "export function f() {}\nexport const c = 1;\nexport { f as g, c };\n"
        "export default function () {}\nmodule.exports = {};\n"
    )
    assert js(src).exports == ["f", "c", "g", "default", "module.exports"]


# ---------------------------------------------------------------------- JSX


def test_jsx_text_with_apostrophes_slashes_and_keywords_is_not_code():
    src = (
        "function App() {\n"
        "  return (\n"
        "    <div className=\"a\">\n"
        "      <p>Don't use // this {\"}\"} if you want</p>\n"
        "      {ok && <b>for free</b>}\n"
        "      <input onChange={e => { set(e.target.value); }} />\n"
        "    </div>\n"
        "  );\n"
        "}\n"
        "const Frag = () => <>hi {name}</>;\n"
    )
    tree = js(src, jsx=True)
    assert [f.name for f in tree.functions] == ["App", "Frag"]
    assert "for free" not in tree.functions[0].body and "Don" not in tree.functions[0].body
    assert "ok &&" in tree.functions[0].body  # expression containers stay code


def test_less_than_comparison_is_not_mistaken_for_jsx():
    tree = js("function f(a, b) { return a < b ? 1 : 2; }\n", jsx=True)
    assert [f.name for f in tree.functions] == ["f"]


# ------------------------------------------------------------------ TypeScript


def test_ts_interface_extends_type_alias_enum_namespace():
    src = (
        "export interface A<T> extends B, C<T> {\n"
        "  id: number; name?: string; run(x: T): void;\n"
        "  readonly tags: Array<string>;\n"
        "  cb: (x: number) => void\n"
        "}\n"
        "type Id = string | number;\n"
        "type Fn<T> = (a: T) => { ok: boolean };\n"
        "enum Color { Red, Green }\n"
        "namespace Outer.Inner { export function g(): number { return 1; } }\n"
    )
    tree = ts(src)
    a = cls(tree, "A")
    assert (a.kind, a.bases) == ("interface", ["B", "C"])
    assert a.fields == ["id", "name", "tags", "cb"] and a.methods == ["run"]
    assert tree.type_aliases == ["Id", "Fn"]
    assert tree.enums == ["Color"] and tree.namespaces == ["Outer.Inner"]
    assert [f.name for f in tree.functions] == ["g"]  # function *types* are not functions


def test_ts_class_extends_typed_fields_and_access_modifiers():
    src = (
        "interface User { id: number; name: string; }\n"
        "class Admin extends User {\n"
        "    private level: number;\n"
        "}\n"
    )
    tree = ts(src)
    assert cls(tree, "User").kind == "interface"
    admin = cls(tree, "Admin")
    assert (admin.kind, admin.bases, admin.fields) == ("class", ["User"], ["level"])


def test_ts_class_full_featured():
    src = (
        "export abstract class Repo<T> extends Base<T> implements IRepo, Other {\n"
        "    private readonly items: T[] = [];\n"
        "    protected static instance?: Repo<any>;\n"
        "    public name!: string;\n"
        "    handler = (e: Event): void => { this.items.length; };\n"
        "    constructor(private svc: Service, public readonly label: string = 'x') { super(); }\n"
        "    abstract find(id: number): T | undefined;\n"
        "    get size(): number { return this.items.length; }\n"
        "    public add(item: T): void { this.items.push(item); }\n"
        "    private async load<K>(k: K): Promise<K> { return k; }\n"
        "    [key: string]: any;\n"
        "}\n"
    )
    repo = cls(ts(src), "Repo")
    assert repo.bases == ["Base"] and repo.implements == ["IRepo", "Other"]
    assert repo.methods == ["handler", "find", "size", "add", "load"]
    assert repo.fields == ["items", "instance", "name", "svc", "label"]  # incl. parameter properties


def test_ts_typed_functions_overloads_and_generics():
    src = (
        "function add(a: number, b: number): number { return a + b; }\n"
        "function over(a: string): string;\n"
        "function over(a: number): number;\n"
        "function over(a: any): any { return a; }\n"
        "const typed: (x: number) => string = (x) => String(x);\n"
        "const gen = <T>(x: T): T => x;\n"
        "const withRet = (a: number): Promise<void> => { return Promise.resolve(); };\n"
        "async function f<T extends object>(a: T): Promise<{ ok: boolean }> { return { ok: true }; }\n"
        "const cb: Array<() => void> = [() => 1];\n"
    )
    names = [f.name for f in ts(src).functions]
    assert names == ["add", "over", "typed", "gen", "withRet", "f", "<anonymous>"]


def test_ts_imports_and_exports():
    src = (
        "import type { Foo } from './foo';\n"
        "import { Bar } from \"../bar\";\n"
        "export * from './all';\n"
        "export interface I {}\nexport type T = number;\nexport default class C {}\n"
    )
    tree = ts(src)
    assert tree.imports == ["../bar", "./all", "./foo"]
    assert {"Foo", "Bar"} <= tree.import_bindings
    assert tree.exports == ["I", "T", "C", "default", "*"]


def test_tsx_generic_arrow_and_jsx_in_same_file():
    src = (
        "const id = <T,>(x: T) => x;\n"
        "const C = <T extends object>(p: T) => <div>{p && \"a\"}</div>;\n"
        "const D: React.FC<Props> = ({ a }) => { return <span>it's {a ? 'y' : 'n'}</span>; };\n"
    )
    assert [f.name for f in ts(src, jsx=True).functions] == ["id", "C", "D"]


def test_ts_difficult_braces_and_comment_markers_in_strings_and_templates():
    src = (
        "interface Cfg { path: string; }\n"
        "const render = (c: Cfg): string => {\n"
        "    const a = \"// not a comment { \";\n"
        "    const b = `}} ${ c.path ? '/* x */' : \"{\" } {{`;\n"
        "    return a + b; // real comment {\n"
        "};\n"
        "class Svc { run(): void { render({ path: '}' }); } }\n"
    )
    tree = ts(src)
    assert [(f.name, f.owner_class) for f in tree.functions] == [("render", None), ("run", "Svc")]
    assert cls(tree, "Cfg").fields == ["path"]


# ------------------------------------------------------------------ errors


@pytest.mark.parametrize("src", ["function f() { return 1;\n", "const x = (1 + 2;\n", "}\n"])
def test_unbalanced_brackets_raise_parse_error(src):
    with pytest.raises(JsFamilyParseError):
        js(src)
