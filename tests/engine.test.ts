import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';
import { Engine, computeMaintainabilityIndex, languageFromPath } from '../src/analyzer/Engine.js';
import { healthStatusForScore, riskLevelForScore } from '../src/domain/bands.js';

const engine = new Engine();

describe('LOC', () => {
  it('python: docstring lines are code, # lines are comments', () => {
    expect(engine.calculateLocFromSource('"""doc\nmore\n"""\n# c\n\nx = 1\n', 'python')).toEqual({
      total: 6,
      code: 4,
      comments: 1,
      blank: 1,
    });
  });

  it('java: line and block comments', () => {
    expect(engine.calculateLocFromSource('/* a\n b */\n// c\n\nint x;\n/** d */\n', 'java')).toEqual({
      total: 6,
      code: 1,
      comments: 4,
      blank: 1,
    });
  });

  it('streamed result equals in-memory result and total = code + comments + blank', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'loc-'));
    try {
      const text = '// header\n\nconst a = 1;\n/* x\n y */\nfunction f() {}\n';
      const path = join(dir, 'a.js');
      await writeFile(path, text);
      const streamed = await engine.calculateLoc(path, 'javascript');
      expect(streamed).toEqual(engine.calculateLocFromSource(text, 'javascript'));
      expect(streamed.total).toBe(streamed.code + streamed.comments + streamed.blank);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe('cyclomatic complexity (tree-sitter)', () => {
  it('python: 1 + if + and + for = 4', async () => {
    const src = 'def f(a):\n    if a and a > 1:\n        return 1\n    for i in range(3):\n        pass\n    return 0\n';
    const r = await engine.calculateComplexity(src, 'python');
    expect(r.functions).toEqual([{ name: 'f', complexity: 4, ownerClass: null }]);
    expect(r.hasSyntaxError).toBe(false);
  });

  it('java: 1 + if + && + for + ternary = 5; abstract methods are skipped', async () => {
    const src = `abstract class A {
      abstract void skip();
      int m(int x) {
        if (x > 0 && x < 5) { return 1; }
        for (int i = 0; i < x; i++) { }
        return x > 2 ? 1 : 0;
      }
    }`;
    const r = await engine.calculateComplexity(src, 'java');
    expect(r.functions).toEqual([{ name: 'm', complexity: 5, ownerClass: 'A' }]);
  });

  it('javascript: counts if / for / while / ?? / arrow functions', async () => {
    const src = 'const f = (a) => { if (a ?? 0) { while (a) { a--; } } return a; };';
    const r = await engine.calculateComplexity(src, 'javascript');
    expect(r.functions).toEqual([{ name: 'f', complexity: 4, ownerClass: null }]);
  });

  it('handles sources larger than the default 32 KB parse buffer', async () => {
    const src = Array.from({ length: 2000 }, (_, i) => `def f${i}(a):\n    return a\n`).join('');
    expect((await engine.calculateComplexity(src, 'python')).functions).toHaveLength(2000);
  });
});

describe('engine plumbing', () => {
  it('maps extensions to languages', () => {
    expect(languageFromPath('x/Foo.java')).toBe('java');
    expect(languageFromPath('a.py')).toBe('python');
    expect(languageFromPath('a.mjs')).toBe('javascript');
    expect(languageFromPath('a.rb')).toBeNull();
  });

  it('analyzeProject never throws: a missing file is reported per file', async () => {
    const [result] = await engine.analyzeProject([
      { absolutePath: '/definitely/not/here.py', relativePath: 'here.py', language: 'python' },
    ]);
    expect(result?.metrics).toBeNull();
    expect(result?.error).toMatch(/ENOENT/);
  });

  it('maintainability index is clamped to 0-100 and 100 for empty input', () => {
    expect(computeMaintainabilityIndex({ halsteadVolume: 0, cyclomaticTotal: 0, sloc: 0, commentLines: 0 })).toBe(100);
    const mi = computeMaintainabilityIndex({ halsteadVolume: 5000, cyclomaticTotal: 40, sloc: 300, commentLines: 20 });
    expect(mi).toBeGreaterThanOrEqual(0);
    expect(mi).toBeLessThanOrEqual(100);
  });

  it('score bands match the Python backend', () => {
    expect(riskLevelForScore(29)).toBe('LOW');
    expect(riskLevelForScore(60)).toBe('HIGH');
    expect(riskLevelForScore(80)).toBe('CRITICAL');
    expect(healthStatusForScore(74)).toBe('NEEDS_ATTENTION');
    expect(healthStatusForScore(90)).toBe('EXCELLENT');
  });
});
