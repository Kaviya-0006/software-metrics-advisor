// Run with: npm test (Node's built-in test runner).
// The fixture is the real output of backend/app/analyzer/comparison.py on the
// controlled V1/V2 fixtures (backend/tests/comparison_fixtures.py).
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import {
  METRIC_CARDS,
  describeCard,
  fileFilterOptions,
  filterFiles,
  formatFileMetric,
  formatNumber,
  formatPercentChange,
  formatSigned,
  resolvedSelection,
  riskTransition,
  versionOptionLabel,
} from "../src/utils/comparisonView.js";

const load = (name) => JSON.parse(readFileSync(new URL(`./fixtures/${name}`, import.meta.url), "utf8"));
const data = load("comparison-v1-v2.json");
const comparison = data.comparison;
const items = comparison.files.items;
const card = (key) => describeCard(METRIC_CARDS.find((c) => c.key === key), comparison.project.metrics[key]);

test("metric cards show the stored previous/current values, differences and percentages", () => {
  const health = card("health_score");
  assert.deepEqual([health.previous, health.current, health.delta, health.percent, health.tone], ["70.0", "60.0", "-10.0", "-14.3%", "bad"]);
  assert.equal(health.significant, true);

  const complexity = card("average_complexity");
  assert.deepEqual([complexity.previous, complexity.current, complexity.delta, complexity.percent], ["3.00", "4.50", "+1.50", "+50.0%"]);
  assert.equal(complexity.tone, "bad"); // complexity went up

  const dup = card("duplication_percentage");
  assert.deepEqual([dup.previous, dup.current, dup.delta], ["4.0%", "7.0%", "+3.0%"]);

  const loc = card("total_loc");
  assert.deepEqual([loc.previous, loc.current, loc.delta, loc.tone], ["400", "460", "+60", "neutral"]); // LOC has no good/bad direction
  assert.equal(card("files_analyzed").delta, "+1");
});

test("zero denominators and missing values never render as 0 or Infinity", () => {
  const risky = card("high_risk_modules"); // previous is 0
  assert.deepEqual([risky.previous, risky.current, risky.delta], ["0", "3", "+3"]);
  assert.equal(risky.percent, "n/a (previous is 0)");

  const missing = describeCard(METRIC_CARDS[0], { previous: null, current: 55, direction: "unavailable", percent_note: "not available (previous value not recorded)" });
  assert.deepEqual([missing.previous, missing.current, missing.delta, missing.percent, missing.tone, missing.word], ["N/A", "55.0", "N/A", "N/A", "na", "Not available"]);
  assert.equal(formatNumber(undefined), "N/A");
  assert.equal(formatSigned(null), "N/A");
  assert.equal(formatPercentChange({ direction: "unchanged", percent_change: 0 }), "0%");
});

test("the fixture carries the known expected changes", () => {
  const byPath = Object.fromEntries(items.map((r) => [r.path, r]));
  assert.equal(byPath["app/stable.py"].status, "unchanged");
  assert.equal(byPath["app/hot.py"].verdict, "regressed");
  assert.equal(byPath["app/clean.py"].verdict, "improved");
  assert.equal(byPath["app/deleted.py"].status, "removed");
  assert.equal(byPath["app/brand_new.py"].status, "added");
  assert.equal(byPath["web/app.ts"].status, "unverified");
  assert.equal(comparison.files.possible_renames[0].confidence, "high");
  assert.ok(comparison.uncertainty.some((u) => u.includes("look like renames")));
});

test("file metric cells and risk transitions", () => {
  const hot = items.find((r) => r.path === "app/hot.py");
  assert.equal(formatFileMetric(hot.changes.complexity_total, 0), "10 → 30 (+20)");
  assert.equal(riskTransition(hot), "LOW → MEDIUM");
  const stable = items.find((r) => r.path === "app/stable.py");
  assert.equal(formatFileMetric(stable.changes.complexity_total, 0), "N/A"); // unchanged files carry no change cells
  assert.equal(riskTransition(items.find((r) => r.path === "app/deleted.py")), "CRITICAL");
});

test("language filter", () => {
  const options = fileFilterOptions(items);
  assert.deepEqual(options.languages, ["java", "python", "typescript"]);
  assert.deepEqual(filterFiles(items, { language: "java" }).map((r) => r.path), ["svc/Api.java"]);
  assert.ok(filterFiles(items, { language: "python" }).every((r) => r.language === "python"));
});

test("risk filter uses the current level (previous for removed files)", () => {
  const critical = filterFiles(items, { riskLevel: "CRITICAL" }).map((r) => r.path).sort();
  assert.deepEqual(critical, ["app/brand_new.py", "app/deleted.py"]);
  const high = filterFiles(items, { riskLevel: "HIGH" }).map((r) => r.path).sort();
  assert.deepEqual(high, ["app/risky.py", "svc/Api.java"]);
  assert.deepEqual(fileFilterOptions(items).risks, ["LOW", "MEDIUM", "HIGH", "CRITICAL"]);
});

test("status, verdict and path filters combine", () => {
  assert.deepEqual(filterFiles(items, { status: "added" }).map((r) => r.path).sort(), ["app/brand_new.py", "app/new_name.py", "svc/Api.java"]);
  assert.deepEqual(filterFiles(items, { verdict: "regressed" }).map((r) => r.path).sort(), ["app/hot.py", "app/risky.py", "app/tight.py"]);
  assert.deepEqual(filterFiles(items, { status: "changed", language: "python", query: "HOT" }).map((r) => r.path), ["app/hot.py"]);
  assert.equal(filterFiles(items, { language: "java", status: "removed" }).length, 0);
  assert.equal(filterFiles(items).length, items.length);
});

test("selectors default to the API's resolved pair and list real versions", () => {
  assert.deepEqual(resolvedSelection(data), { from: 1, to: 2 });
  assert.deepEqual(resolvedSelection(load("comparison-first-version.json")), { from: null, to: null });
  assert.match(versionOptionLabel(data.versions[1]), /^V2 - .* - health 60\.0$/);
});

test("first-version empty state is a normal response with an explanation", () => {
  const first = load("comparison-first-version.json");
  assert.equal(first.available, false);
  assert.equal(first.comparison, null);
  assert.match(first.reason, /first version/);
  assert.equal(first.versions.length, 1);
});

test("regression alerts and improvements are sorted by severity and explained", () => {
  const ranks = { high: 3, medium: 2, low: 1 };
  const sevs = comparison.regressions.map((a) => ranks[a.severity]);
  assert.deepEqual(sevs, [...sevs].sort((x, y) => y - x));
  assert.ok(comparison.regressions.every((a) => a.title && a.detail));
  assert.ok(comparison.improvements.some((a) => a.id === "file:app/clean.py"));
  assert.ok(comparison.architecture.regressions.every((a) => a.evidence.length > 0));
});
