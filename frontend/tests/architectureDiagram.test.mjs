// Run with: npm test   (Node's built-in test runner; no extra dependencies)
import test from "node:test";
import assert from "node:assert/strict";
import { escapeXml, layoutDiagram, renderArchitectureSvg } from "../src/utils/architectureDiagram.js";

const node = (id, extra = {}) => ({
  id, layer: id, label: id, files: 2, loc: 40, languages: { python: 2 }, confidence: "high",
  unmatched_calls: 0, unused_endpoints: 0, uncertain: 0, internal_matches: 0, ...extra,
});

const fullStack = {
  nodes: [node("frontend", { languages: { javascript: 3 }, unmatched_calls: 1 }), node("backend", { unused_endpoints: 2 })],
  edges: [{ source: "frontend", target: "backend", kind: "api", label: "3 matched API calls", count: 3, confidence: "medium" }],
};

test("every node and edge of the graph appears in the SVG, and nothing else", () => {
  const svg = renderArchitectureSvg(fullStack);
  assert.match(svg, /^<svg [^>]*xmlns="http:\/\/www\.w3\.org\/2000\/svg"/);
  assert.equal((svg.match(/data-layer="/g) || []).length, 2);
  assert.equal((svg.match(/data-edge="/g) || []).length, 1);
  assert.ok(svg.includes("3 matched API calls"));
  assert.ok(svg.includes("javascript 3"));
  assert.ok(svg.includes("1 unmatched call"));
  assert.ok(svg.includes("2 potentially unused"));
});

test("a backend-only graph draws boxes but no arrows and says so", () => {
  const svg = renderArchitectureSvg({ nodes: [node("backend")], edges: [] });
  assert.equal((svg.match(/data-edge="/g) || []).length, 0);
  assert.ok(svg.includes("no arrows are drawn"));
});

test("frontend-only graph renders without an edge target", () => {
  const svg = renderArchitectureSvg({ nodes: [node("frontend")], edges: [] });
  assert.equal((svg.match(/data-layer="/g) || []).length, 1);
});

test("edges whose endpoints are not nodes are skipped instead of crashing", () => {
  const svg = renderArchitectureSvg({ nodes: [node("frontend")], edges: [{ source: "frontend", target: "ghost", label: "x", confidence: "low" }] });
  assert.equal((svg.match(/data-edge="/g) || []).length, 0);
});

test("labels from uploaded projects are XML-escaped (no markup injection)", () => {
  const evil = '"><script>alert(1)</script>';
  const svg = renderArchitectureSvg({ nodes: [node("frontend", { label: evil, languages: { [evil]: 1 } })], edges: [] }, { title: evil });
  assert.ok(!svg.includes("<script>"));
  assert.ok(svg.includes("&lt;script&gt;"));
  assert.equal(escapeXml(`a&b<c>"d'`), "a&amp;b&lt;c&gt;&quot;d&apos;");
});

test("unknown layers are placed on a second row and the canvas fits all of them", () => {
  const diagram = { nodes: [node("frontend"), node("tests"), node("unknown"), node("weird")], edges: [] };
  const { positions, width, height } = layoutDiagram(diagram);
  assert.equal(positions.size, 4);
  assert.ok(positions.get("tests").y > positions.get("frontend").y);
  for (const { x, y } of positions.values()) {
    assert.ok(x + 210 <= width && y + 104 <= height);
  }
});

test("empty diagram still yields a valid SVG", () => {
  const svg = renderArchitectureSvg({ nodes: [], edges: [] });
  assert.ok(svg.startsWith("<svg") && svg.endsWith("</svg>"));
});
