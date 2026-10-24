/**
 * Engine: asynchronous metrics engine
 * ===================================
 * Ports the Python metric pipeline (loc.py, complexity.py, maintainability.py)
 * to Node.js with strict types.
 *
 *   1. LOC         async line-by-line STREAMING through fs/promises
 *                  (FileHandle#readLines): flat memory, even for huge files.
 *   2. Complexity  tree-sitter parse + ONE S-expression query that captures
 *                  every function and every decision point; each decision is
 *                  attributed to its nearest enclosing function.
 *   3. Maintain-   the same Oman/Hagemeister-style formula radon uses,
 *      ability     fed by a Halstead volume computed from tree-sitter leaves.
 *
 * Definitions (same as docs/metrics.md in the Python project):
 *     total = code + comments + blank            (always holds)
 *     CC    = 1 + number of decision points      (McCabe, per function)
 *
 * MIGRATION NOTES
 *  - radon (Python) and javalang (Java) are replaced by ONE mechanism for all
 *    languages. Adding a language = a grammar package + one LANGUAGES entry.
 *  - tree-sitter and grammars are loaded lazily (dynamic import), so LOC works
 *    even when the native build is unavailable, and a missing grammar fails
 *    only the files of that language (reported per file, never fatal).
 *  - parse() is synchronous CPU work. For very large uploads run Engine inside
 *    worker_threads (e.g. Piscina) so the HTTP event loop stays responsive;
 *    nothing here needs to change for that.
 *  - There are no type assertions: the grammar loader is an exhaustive switch,
 *    queries are typed, and optional navigation uses `?.` / null checks.
 */
import { open, readFile } from 'node:fs/promises';
import type Parser from 'tree-sitter';
import type {
  ComplexityMetrics,
  FunctionComplexity,
  LocMetrics,
  ModuleAnalysisResult,
  SourceFileRef,
  SupportedLanguage,
} from '../types/metrics.js';

// ===========================================================================
// Language registry
// ===========================================================================

interface LanguageSpec {
  commentStyle: 'python' | 'c';
  /** Node types that delimit a function, for attributing decisions. */
  functionTypes: ReadonlySet<string>;
  /** Node types whose `name` field becomes `ownerClass`. */
  classTypes: ReadonlySet<string>;
  /** Captures: `@fn` (a function with a body) and `@decision` (+1 path). */
  query: string;
}

const LANGUAGES: Readonly<Record<SupportedLanguage, LanguageSpec>> = {
  python: {
    commentStyle: 'python',
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
    commentStyle: 'c',
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
    commentStyle: 'c',
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
};

const EXTENSIONS: Readonly<Record<string, SupportedLanguage>> = {
  '.py': 'python',
  '.java': 'java',
  '.js': 'javascript',
  '.mjs': 'javascript',
  '.cjs': 'javascript',
  '.jsx': 'javascript',
};

/** Language for a file path, or null when unsupported. */
export function languageFromPath(path: string): SupportedLanguage | null {
  const dot = path.lastIndexOf('.');
  if (dot < 0) return null;
  return EXTENSIONS[path.slice(dot).toLowerCase()] ?? null;
}

// ===========================================================================
// 1. Lines of Code
// ===========================================================================

/**
 * Incremental line classifier: feed it one line at a time.
 *
 * Parity with the Python implementation:
 *  - Python: lines starting with `#` are comments. Lines inside triple-quoted
 *    strings (docstrings included) count as CODE, like radon's `sloc + multi`,
 *    so total === code + comments + blank always holds.
 *  - Java / JavaScript ('c' style): `//` lines and `/* ... *\/` lines are
 *    comments. A line that closes a block comment is a comment line even if
 *    code follows (documented approximation).
 */
class LocClassifier {
  private total = 0;
  private code = 0;
  private comments = 0;
  private blank = 0;
  private inBlockComment = false; // c-style
  private openTripleQuote: string | null = null; // python
  private readonly style: LanguageSpec['commentStyle'];

  constructor(style: LanguageSpec['commentStyle']) {
    this.style = style;
  }

  push(rawLine: string): void {
    this.total++;
    const line = rawLine.trim();
    if (this.style === 'python') this.classifyPython(line);
    else this.classifyC(line);
  }

  result(): LocMetrics {
    return { total: this.total, code: this.code, comments: this.comments, blank: this.blank };
  }

  private classifyC(line: string): void {
    if (this.inBlockComment) {
      this.comments++;
      if (line.includes('*/')) this.inBlockComment = false;
      return;
    }
    if (line === '') {
      this.blank++;
    } else if (line.startsWith('//')) {
      this.comments++;
    } else if (line.startsWith('/*')) {
      this.comments++;
      if (!line.includes('*/', 2)) this.inBlockComment = true;
    } else {
      this.code++;
    }
  }

  private classifyPython(line: string): void {
    if (this.openTripleQuote !== null) {
      this.code++;
      if (line.includes(this.openTripleQuote)) this.openTripleQuote = null;
      return;
    }
    if (line === '') {
      this.blank++;
      return;
    }
    if (line.startsWith('#')) {
      this.comments++;
      return;
    }
    this.code++;
    const match = /"""|'''/.exec(line);
    if (match) {
      const delimiter = match[0];
      const occurrences = line.split(delimiter).length - 1;
      if (occurrences % 2 === 1) this.openTripleQuote = delimiter; // opened, not closed here
    }
  }
}

// ===========================================================================
// 2 + 3. tree-sitter runtime
// ===========================================================================

interface ParserRuntime {
  parser: Parser;
  query: Parser.Query;
}

/** Exhaustive switch: adding a language to SupportedLanguage fails to compile until handled. */
async function loadGrammar(language: SupportedLanguage): Promise<Parser.Language> {
  switch (language) {
    case 'python':
      return (await import('tree-sitter-python')).default;
    case 'java':
      return (await import('tree-sitter-java')).default;
    case 'javascript':
      return (await import('tree-sitter-javascript')).default;
  }
}

/** Internal result of one parse: the public complexity plus the Halstead volume. */
interface SyntaxAnalysis {
  complexity: ComplexityMetrics;
  halsteadVolume: number;
}

export interface EngineOptions {
  /** Max files processed at once; keeps open file descriptors bounded. */
  concurrency?: number;
}

export class Engine {
  private readonly concurrency: number;
  /** language -> Promise<runtime>. Parsers/queries are costly: build once, share. */
  private readonly runtimes = new Map<SupportedLanguage, Promise<ParserRuntime>>();

  constructor(options: EngineOptions = {}) {
    this.concurrency = Math.max(1, options.concurrency ?? 8);
  }

  // -------------------------------------------------------------------------
  // LOC
  // -------------------------------------------------------------------------

  /** Streams the file line by line; memory use does not depend on file size. */
  async calculateLoc(path: string, language: SupportedLanguage): Promise<LocMetrics> {
    const classifier = new LocClassifier(LANGUAGES[language].commentStyle);
    const handle = await open(path, 'r');
    try {
      let first = true;
      // readLines() closes the handle itself when the stream ends.
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
  calculateLocFromSource(source: string, language: SupportedLanguage): LocMetrics {
    const classifier = new LocClassifier(LANGUAGES[language].commentStyle);
    const lines = source.split(/\r\n|\n|\r/);
    if (lines.at(-1) === '') lines.pop(); // a trailing newline is not an extra line
    lines.forEach((line, i) => classifier.push(i === 0 ? line.replace(/^\uFEFF/, '') : line));
    return classifier.result();
  }

  // -------------------------------------------------------------------------
  // Cyclomatic complexity
  // -------------------------------------------------------------------------

  /**
   * CC = 1 + decision points, per function.
   *
   * The query runs ONCE over the whole tree (a single O(n) pass); each
   * captured decision node then walks up its parents to the nearest enclosing
   * function. Nested functions get their own score rather than inflating the
   * outer one.
   */
  async calculateComplexity(source: string, language: SupportedLanguage): Promise<ComplexityMetrics> {
    return (await this.analyzeSyntax(source, language)).complexity;
  }

  private getRuntime(language: SupportedLanguage): Promise<ParserRuntime> {
    const cached = this.runtimes.get(language);
    if (cached) return cached;

    const pending = (async (): Promise<ParserRuntime> => {
      const [{ default: TreeSitter }, grammar] = await Promise.all([import('tree-sitter'), loadGrammar(language)]);
      const parser = new TreeSitter();
      parser.setLanguage(grammar);
      return { parser, query: new TreeSitter.Query(grammar, LANGUAGES[language].query) };
    })();

    // Don't cache a failure forever (e.g. a grammar installed after startup).
    pending.catch(() => this.runtimes.delete(language));
    this.runtimes.set(language, pending);
    return pending;
  }

  private async analyzeSyntax(source: string, language: SupportedLanguage): Promise<SyntaxAnalysis> {
    const spec = LANGUAGES[language];
    const { parser, query } = await this.getRuntime(language);

    // node-tree-sitter's default 32 KB buffer makes parse() throw
    // "Invalid argument" on larger files; size it to the input.
    const tree = parser.parse(source, undefined, { bufferSize: source.length * 2 + 1024 });

    const functions = new Map<number, FunctionComplexity>(); // keyed by node.id
    const decisions: Parser.SyntaxNode[] = [];

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

    const { volume, hasError } = halstead(tree.rootNode);
    return {
      complexity: summarize([...functions.values()], hasError),
      halsteadVolume: volume,
    };
  }

  // -------------------------------------------------------------------------
  // Orchestration
  // -------------------------------------------------------------------------

  /** Analyses one file. NEVER throws: failures are returned as `error`. */
  async analyzeFile(file: SourceFileRef): Promise<ModuleAnalysisResult> {
    try {
      const [loc, syntax] = await Promise.all([
        this.calculateLoc(file.absolutePath, file.language), // streamed
        readFile(file.absolutePath, 'utf8').then((source) => this.analyzeSyntax(source, file.language)),
      ]);
      const maintainabilityIndex = computeMaintainabilityIndex({
        halsteadVolume: syntax.halsteadVolume,
        cyclomaticTotal: syntax.complexity.total,
        sloc: loc.code,
        commentLines: loc.comments,
      });
      return { file, metrics: { loc, complexity: syntax.complexity, maintainabilityIndex }, error: null };
    } catch (err) {
      return { file, metrics: null, error: err instanceof Error ? err.message : String(err) };
    }
  }

  /**
   * Analyses many files with BOUNDED concurrency so a 5,000-file project can't
   * exhaust file descriptors. Result order matches input order.
   */
  async analyzeProject(files: readonly SourceFileRef[]): Promise<ModuleAnalysisResult[]> {
    const results: ModuleAnalysisResult[] = [];
    // One shared iterator: every worker pulls the next unclaimed file.
    const queue = files.entries();
    const worker = async (): Promise<void> => {
      for (const [index, file] of queue) {
        results[index] = await this.analyzeFile(file);
      }
    };
    await Promise.all(Array.from({ length: Math.min(this.concurrency, files.length) }, worker));
    return results;
  }
}

// ===========================================================================
// Tree helpers (pure functions over tree-sitter nodes)
// ===========================================================================

function functionName(node: Parser.SyntaxNode): string {
  const direct = node.childForFieldName('name');
  if (direct) return direct.text;
  // const foo = () => {...}: the name lives on the variable declarator
  const parent = node.parent;
  if (parent?.type === 'variable_declarator') return parent.childForFieldName('name')?.text ?? '<anonymous>';
  return '<anonymous>';
}

function ownerClassName(node: Parser.SyntaxNode, spec: LanguageSpec): string | null {
  for (let n = node.parent; n; n = n.parent) {
    if (spec.classTypes.has(n.type)) return n.childForFieldName('name')?.text ?? null;
  }
  return null;
}

function enclosingFunction(node: Parser.SyntaxNode, spec: LanguageSpec): Parser.SyntaxNode | null {
  for (let n = node.parent; n; n = n.parent) {
    if (spec.functionTypes.has(n.type)) return n;
  }
  return null;
}

function summarize(functions: FunctionComplexity[], hasSyntaxError: boolean): ComplexityMetrics {
  if (functions.length === 0) {
    // Nothing to measure -> 0, not a fabricated 1 (same rule as Python).
    return { total: 0, average: 0, maximum: 0, functions, hasSyntaxError };
  }
  const values = functions.map((f) => f.complexity);
  const total = values.reduce((a, b) => a + b, 0);
  return {
    total,
    average: Math.round((total / values.length) * 100) / 100,
    maximum: Math.max(...values),
    functions,
    hasSyntaxError,
  };
}

/**
 * Halstead volume from the token (leaf) stream, plus a syntax-error flag.
 * Iterative (explicit stack) so deeply nested code cannot overflow the call stack.
 *
 * Classification: an anonymous leaf's `type` equals its own text ("if", "&&",
 * "(") -> operator; a named leaf (identifier, number, string...) -> operand.
 * Comments are skipped. Same simplification as the Python Java implementation.
 */
function halstead(root: Parser.SyntaxNode): { volume: number; hasError: boolean } {
  const operators = new Map<string, number>();
  const operands = new Map<string, number>();
  const bump = (map: Map<string, number>, key: string): void => {
    map.set(key, (map.get(key) ?? 0) + 1);
  };

  let hasError = false;
  const stack: Parser.SyntaxNode[] = [root];
  for (let node = stack.pop(); node; node = stack.pop()) {
    if (node.type === 'ERROR') hasError = true;
    if (node.childCount > 0) {
      stack.push(...node.children);
    } else if (!node.type.includes('comment')) {
      bump(node.type === node.text ? operators : operands, node.text);
    }
  }

  const distinct = operators.size + operands.size;
  const length = sum(operators.values()) + sum(operands.values());
  const volume = distinct <= 0 || length <= 0 ? 0 : length * Math.log2(distinct);
  return { volume, hasError };
}

const sum = (values: Iterable<number>): number => {
  let total = 0;
  for (const v of values) total += v;
  return total;
};

// ===========================================================================
// Maintainability Index (port of _mi_compute in maintainability.py)
// ===========================================================================

export interface MaintainabilityInput {
  halsteadVolume: number;
  cyclomaticTotal: number;
  /** Source (code) lines. */
  sloc: number;
  commentLines: number;
}

/**
 * MI = clamp( (171 - 5.2 ln V - 0.23 CC - 16.2 ln SLOC + 50 sin sqrt(2.46 * rad(comment%))) * 100/171, 0, 100 )
 * Returns 100 when there is no volume or no code, like the Python version.
 * Because Halstead tokens come from tree-sitter rather than radon/javalang,
 * values are close to, but not identical with, the Python backend's.
 */
export function computeMaintainabilityIndex(input: MaintainabilityInput): number {
  const { halsteadVolume, cyclomaticTotal, sloc, commentLines } = input;
  if (halsteadVolume <= 0 || sloc <= 0) return 100;

  const commentPercent = (commentLines / sloc) * 100;
  const commentsScale = Math.sqrt(2.46 * ((commentPercent * Math.PI) / 180));
  const raw =
    171 -
    5.2 * Math.log(halsteadVolume) -
    0.23 * cyclomaticTotal -
    16.2 * Math.log(sloc) +
    50 * Math.sin(commentsScale);
  const scaled = Math.min(Math.max(0, (raw * 100) / 171), 100);
  return Math.round(scaled * 100) / 100;
}
