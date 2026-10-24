/**
 * Metrics engine  (replaces app/analyzer/metrics/loc.py + complexity.py)
 * ======================================================================
 *
 *  1. LOC         - async line-by-line STREAMING through fs/promises
 *                   (FileHandle#readLines): memory stays flat even for
 *                   huge generated files.
 *  2. Cyclomatic  - tree-sitter parse + one S-expression query. Every
 *                   function and every decision point is captured in a
 *                   single pass over the tree; each decision is then
 *                   attributed to its nearest enclosing function.
 *
 * Definitions (same as docs/metrics.md in the Python project):
 *     total = code + comments + blank            (always holds)
 *     CC    = 1 + number of decision points      (McCabe, per function)
 *
 * MIGRATION NOTES
 *  - Python used `radon` (Python) and `javalang` (Java). tree-sitter replaces
 *    both with ONE mechanism for every language: add a language by adding a
 *    grammar package and a LANGUAGES entry below.
 *  - Grammars are loaded lazily with dynamic import(). LOC works even if the
 *    native tree-sitter build is unavailable, and a missing grammar only
 *    fails the files of that language (reported per file, never fatal).
 *  - tree-sitter's parse() is synchronous CPU work. For very large uploads,
 *    run analyzeFile inside worker_threads (e.g. Piscina) so the HTTP event
 *    loop stays responsive. The functions here are pure, so that move needs
 *    no change to them.
 */
import { open, readFile } from 'node:fs/promises';
import { extname } from 'node:path';

// ===========================================================================
// Language registry
// ===========================================================================

/**
 * `commentStyle`  : drives the LOC classifier.
 * `grammar`       : npm package that exports the tree-sitter language.
 * `functionTypes` : node types that delimit a function for attribution.
 * `classTypes`    : node types whose name becomes `ownerClass`.
 * `query`         : captures `@fn` (functions) and `@decision` (+1 paths).
 */
export const LANGUAGES = Object.freeze({
  python: {
    extensions: ['.py'],
    commentStyle: 'python',
    grammar: 'tree-sitter-python',
    functionTypes: new Set(['function_definition']),
    classTypes: new Set(['class_definition']),
    query: `
      (function_definition) @fn

      [
        (if_statement)
        (elif_clause)
        (for_statement)
        (while_statement)
        (except_clause)
        (conditional_expression)
        (case_clause)
        (assert_statement)
        (for_in_clause)
        (if_clause)
      ] @decision

      (boolean_operator) @decision
    `,
  },
  java: {
    extensions: ['.java'],
    commentStyle: 'c',
    grammar: 'tree-sitter-java',
    functionTypes: new Set(['method_declaration', 'constructor_declaration']),
    classTypes: new Set(['class_declaration', 'enum_declaration', 'record_declaration']),
    query: `
      ; abstract / interface methods have no body and are skipped
      (method_declaration body: (_)) @fn
      (constructor_declaration) @fn

      [
        (if_statement)
        (for_statement)
        (enhanced_for_statement)
        (while_statement)
        (do_statement)
        (catch_clause)
        (ternary_expression)
      ] @decision

      ; each 'case' label adds a path; 'default' does not
      (switch_label "case") @decision

      ; short-circuit operators add a path
      (binary_expression operator: ["&&" "||"] @decision)
    `,
  },
  javascript: {
    extensions: ['.js', '.mjs', '.cjs', '.jsx'],
    commentStyle: 'c',
    grammar: 'tree-sitter-javascript',
    functionTypes: new Set([
      'function_declaration',
      'generator_function_declaration',
      'method_definition',
      'arrow_function',
    ]),
    classTypes: new Set(['class_declaration']),
    query: `
      (function_declaration) @fn
      (generator_function_declaration) @fn
      (method_definition) @fn
      (arrow_function) @fn

      [
        (if_statement)
        (for_statement)
        (for_in_statement)
        (while_statement)
        (do_statement)
        (catch_clause)
        (ternary_expression)
        (switch_case)
      ] @decision

      (binary_expression operator: ["&&" "||" "??"] @decision)
    `,
  },
});

export const SUPPORTED_LANGUAGES = Object.freeze(Object.keys(LANGUAGES));

/** @returns {string|null} language key for a file path, or null if unsupported */
export function languageFromPath(path) {
  const ext = extname(path).toLowerCase();
  return SUPPORTED_LANGUAGES.find((l) => LANGUAGES[l].extensions.includes(ext)) ?? null;
}

function requireLanguage(language) {
  const spec = LANGUAGES[language];
  if (!spec) throw new Error(`Unsupported language: ${language}`);
  return spec;
}

// ===========================================================================
// 1. Lines of Code
// ===========================================================================

/**
 * Incremental line classifier - feed it one line at a time.
 *
 * Parity with the Python implementation:
 *  - Python: lines starting with `#` are comments. Lines inside triple-quoted
 *    strings (docstrings included) count as CODE, like radon's `sloc + multi`,
 *    so total == code + comments + blank always holds.
 *  - Java / JavaScript ('c' style): `//` lines and `/* ... *\/` lines are
 *    comments. A line that closes a block comment is a comment line even if
 *    code follows (documented approximation).
 */
class LocClassifier {
  #commentStyle;
  #total = 0;
  #code = 0;
  #comments = 0;
  #blank = 0;
  #inBlockComment = false; // c-style
  #openTripleQuote = null; // python

  constructor(commentStyle) {
    this.#commentStyle = commentStyle;
  }

  push(rawLine) {
    this.#total++;
    const line = rawLine.trim();
    if (this.#commentStyle === 'python') this.#python(line);
    else this.#cStyle(line);
  }

  result() {
    return { total: this.#total, code: this.#code, comments: this.#comments, blank: this.#blank };
  }

  #cStyle(line) {
    if (this.#inBlockComment) {
      this.#comments++;
      if (line.includes('*/')) this.#inBlockComment = false;
      return;
    }
    if (line === '') this.#blank++;
    else if (line.startsWith('//')) this.#comments++;
    else if (line.startsWith('/*')) {
      this.#comments++;
      if (!line.includes('*/', 2)) this.#inBlockComment = true;
    } else this.#code++;
  }

  #python(line) {
    if (this.#openTripleQuote !== null) {
      this.#code++;
      if (line.includes(this.#openTripleQuote)) this.#openTripleQuote = null;
      return;
    }
    if (line === '') {
      this.#blank++;
      return;
    }
    if (line.startsWith('#')) {
      this.#comments++;
      return;
    }
    this.#code++;
    const match = /"""|'''/.exec(line);
    if (match) {
      const occurrences = line.split(match[0]).length - 1;
      if (occurrences % 2 === 1) this.#openTripleQuote = match[0]; // opened, not closed on this line
    }
  }
}

/**
 * Streams a file line by line; memory use does not depend on file size.
 * @param {string} path
 * @param {string} language  key of LANGUAGES
 * @returns {Promise<{total:number, code:number, comments:number, blank:number}>}
 */
export async function calculateLocFromFile(path, language) {
  const classifier = new LocClassifier(requireLanguage(language).commentStyle);
  const handle = await open(path, 'r');
  try {
    let first = true;
    // readLines() auto-closes the handle when the stream ends.
    for await (const line of handle.readLines({ encoding: 'utf8' })) {
      classifier.push(first ? line.replace(/^\uFEFF/, '') : line); // strip BOM
      first = false;
    }
    return classifier.result();
  } finally {
    await handle.close(); // no-op if already closed; prevents fd leaks on errors
  }
}

/** Same classification for in-memory text (tests, pasted snippets). */
export function calculateLocFromSource(source, language) {
  const classifier = new LocClassifier(requireLanguage(language).commentStyle);
  const lines = source.split(/\r\n|\n|\r/);
  if (lines.at(-1) === '') lines.pop(); // a trailing newline is not an extra line
  lines.forEach((line, i) => classifier.push(i === 0 ? line.replace(/^\uFEFF/, '') : line));
  return classifier.result();
}

// ===========================================================================
// 2. Cyclomatic Complexity (tree-sitter)
// ===========================================================================

/** language -> Promise<{ parser, query }>; created once, shared by all files. */
const runtimes = new Map();

function loadRuntime(language) {
  let pending = runtimes.get(language);
  if (!pending) {
    const spec = requireLanguage(language);
    pending = (async () => {
      const [{ default: Parser }, { default: grammar }] = await Promise.all([
        import('tree-sitter'),
        import(spec.grammar),
      ]);
      const parser = new Parser();
      parser.setLanguage(grammar);
      return { parser, query: new Parser.Query(grammar, spec.query) };
    })();
    // Don't cache a failure forever (e.g. grammar installed after startup).
    pending.catch(() => runtimes.delete(language));
    runtimes.set(language, pending);
  }
  return pending;
}

function functionName(node) {
  const direct = node.childForFieldName('name');
  if (direct) return direct.text;
  // const foo = () => {...}  -> name comes from the variable declarator
  const parent = node.parent;
  if (parent?.type === 'variable_declarator') return parent.childForFieldName('name')?.text ?? '<anonymous>';
  return '<anonymous>';
}

function ownerClassName(node, spec) {
  for (let n = node.parent; n; n = n.parent) {
    if (spec.classTypes.has(n.type)) return n.childForFieldName('name')?.text ?? null;
  }
  return null;
}

function enclosingFunction(node, spec) {
  for (let n = node.parent; n; n = n.parent) {
    if (spec.functionTypes.has(n.type)) return n;
  }
  return null;
}

/**
 * CC = 1 + decision points, per function.
 *
 * The query runs ONCE over the whole tree (single O(n) pass); each captured
 * decision node then walks up its parents to the nearest enclosing function.
 * Nested functions therefore get their own score instead of inflating the
 * outer one.
 *
 * @param {string} source
 * @param {string} language  key of LANGUAGES
 * @returns {Promise<{total:number, average:number, maximum:number,
 *   perFunction: {name:string, complexity:number, ownerClass:string|null}[],
 *   hasSyntaxError:boolean}>}
 */
export async function calculateCyclomaticComplexity(source, language) {
  const spec = requireLanguage(language);
  const { parser, query } = await loadRuntime(language);

  // node-tree-sitter's default 32 KB buffer makes parse() throw
  // "Invalid argument" on larger files; size it to the input.
  const tree = parser.parse(source, undefined, { bufferSize: source.length * 2 + 1024 });

  const functions = new Map(); // node.id -> { name, complexity, ownerClass }
  const decisions = [];

  for (const { name, node } of query.captures(tree.rootNode)) {
    if (name === 'fn') {
      functions.set(node.id, {
        name: functionName(node),
        complexity: 1, // the base path
        ownerClass: ownerClassName(node, spec),
      });
    } else if (name === 'decision') {
      decisions.push(node);
    }
  }

  for (const decision of decisions) {
    const fn = enclosingFunction(decision, spec);
    const entry = fn ? functions.get(fn.id) : undefined;
    if (entry) entry.complexity += 1; // decisions outside any function are ignored
  }

  const perFunction = [...functions.values()];
  const hasSyntaxError = tree.rootNode.hasError;
  if (perFunction.length === 0) {
    // Nothing to measure -> 0, not a fabricated 1 (same rule as Python).
    return { total: 0, average: 0, maximum: 0, perFunction, hasSyntaxError };
  }
  const values = perFunction.map((f) => f.complexity);
  const total = values.reduce((a, b) => a + b, 0);
  return {
    total,
    average: Math.round((total / values.length) * 100) / 100,
    maximum: Math.max(...values),
    perFunction,
    hasSyntaxError,
  };
}

// ===========================================================================
// 3. Orchestration
// ===========================================================================

/**
 * Analyse one file. NEVER throws: failures come back as `error`, mirroring
 * the Python parse_file() contract (one bad file can't sink a whole scan).
 *
 * @param {{ absolutePath: string, language: string }} file
 */
export async function analyzeFile(file) {
  try {
    const [loc, complexity] = await Promise.all([
      calculateLocFromFile(file.absolutePath, file.language), // streamed
      readFile(file.absolutePath, 'utf8').then((src) => calculateCyclomaticComplexity(src, file.language)),
    ]);
    return { file, metrics: { loc, complexity }, error: null };
  } catch (err) {
    return { file, metrics: null, error: err instanceof Error ? err.message : String(err) };
  }
}

/**
 * Analyse many files with BOUNDED concurrency so a 5,000-file project can't
 * exhaust file descriptors. Result order matches input order.
 *
 * @param {{ absolutePath: string, language: string }[]} files
 * @param {{ concurrency?: number }} [options]
 */
export async function analyzeProject(files, { concurrency = 8 } = {}) {
  const results = new Array(files.length);
  let next = 0;
  const worker = async () => {
    for (let i = next++; i < files.length; i = next++) {
      results[i] = await analyzeFile(files[i]);
    }
  };
  const pool = Math.min(Math.max(1, concurrency), files.length);
  await Promise.all(Array.from({ length: pool }, worker));
  return results;
}
