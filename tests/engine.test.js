import assert from 'node:assert/strict';
import { mkdtemp, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, it } from 'node:test';
import {
  analyzeProject,
  calculateCyclomaticComplexity,
  calculateLocFromFile,
  calculateLocFromSource,
  languageFromPath,
} from '../src/analyzer/engine.js';
import { toIsoUtc } from '../src/database/schema.js';

// tree-sitter is a native module; skip (don't fail) those tests where it isn't installed.
const hasTreeSitter = await Promise.all([import('tree-sitter'), import('tree-sitter-python'), import('tree-sitter-java')])
  .then(() => true)
  .catch(() => false);

describe('LOC', () => {
  it('python: docstring lines are code, # lines are comments', () => {
    const r = calculateLocFromSource('"""doc\nmore\n"""\n# c\n\nx = 1\n', 'python');
    assert.deepEqual(r, { total: 6, code: 4, comments: 1, blank: 1 });
  });

  it('java: line and block comments', () => {
    const r = calculateLocFromSource('/* a\n b */\n// c\n\nint x;\n/** d */\n', 'java');
    assert.deepEqual(r, { total: 6, code: 1, comments: 4, blank: 1 });
  });

  it('streamed file result equals in-memory result, and total = code + comments + blank', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'loc-'));
    try {
      const text = '// header\n\nconst a = 1;\n/* x\n y */\nfunction f() {}\n';
      const path = join(dir, 'a.js');
      await writeFile(path, text);
      const streamed = await calculateLocFromFile(path, 'javascript');
      assert.deepEqual(streamed, calculateLocFromSource(text, 'javascript'));
      assert.equal(streamed.total, streamed.code + streamed.comments + streamed.blank);
    } finally {
      await rm(dir, { recursive: true, force: true });
    }
  });
});

describe('engine plumbing', () => {
  it('maps extensions to languages', () => {
    assert.equal(languageFromPath('x/Foo.java'), 'java');
    assert.equal(languageFromPath('a.py'), 'python');
    assert.equal(languageFromPath('a.mjs'), 'javascript');
    assert.equal(languageFromPath('a.rb'), null);
  });

  it('analyzeProject never throws: a missing file is reported per file', async () => {
    const [r] = await analyzeProject([{ absolutePath: '/definitely/not/here.py', language: 'python' }]);
    assert.equal(r.metrics, null);
    assert.match(r.error, /ENOENT/);
  });

  it('normalises SQLAlchemy-style timestamps to ISO UTC', () => {
    assert.equal(toIsoUtc('2026-10-23 10:15:00.123456'), '2026-10-23T10:15:00.123456Z');
    assert.equal(toIsoUtc('2026-10-23T10:15:00Z'), '2026-10-23T10:15:00Z');
    assert.equal(toIsoUtc(null), null);
  });
});

describe('cyclomatic complexity (tree-sitter)', { skip: !hasTreeSitter && 'tree-sitter not installed' }, () => {
  it('python: 1 + if + and + for = 4', async () => {
    const src = 'def f(a):\n    if a and a > 1:\n        return 1\n    for i in range(3):\n        pass\n    return 0\n';
    const r = await calculateCyclomaticComplexity(src, 'python');
    assert.deepEqual(r.perFunction, [{ name: 'f', complexity: 4, ownerClass: null }]);
  });

  it('java: 1 + if + && + for + ternary = 5; abstract methods skipped', async () => {
    const src = `abstract class A {
      abstract void skip();
      int m(int x) {
        if (x > 0 && x < 5) { return 1; }
        for (int i = 0; i < x; i++) { }
        return x > 2 ? 1 : 0;
      }
    }`;
    const r = await calculateCyclomaticComplexity(src, 'java');
    assert.deepEqual(r.perFunction, [{ name: 'm', complexity: 5, ownerClass: 'A' }]);
  });
});
