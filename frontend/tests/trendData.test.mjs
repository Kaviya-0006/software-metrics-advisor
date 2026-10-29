// Run with: npm test   (Node's built-in test runner)
import test from "node:test";
import assert from "node:assert/strict";
import {
  TREND_METRICS,
  buildSeries,
  buildSeriesForMetric,
  categoryMetric,
  describeChanges,
  formatDelta,
  summarizeSeries,
  yDomain,
} from "../src/utils/trendData.js";

// Rows shaped exactly like GET /api/projects/{id}/history entries.
const row = (version, over = {}) => ({
  scan_id: 100 + version,
  version_number: version,
  scanned_at: `2026-10-0${version}T10:00:00Z`,
  health_score: 60 + version * 5,
  health_status: "GOOD",
  category_scores: { complexity: 50 + version, documentation: 0.0, testing: null },
  metrics: {
    average_complexity: 4 - version * 0.5,
    average_maintainability: 70 + version,
    duplication_percentage: 10 - version,
    average_risk_score: 30 + version * 2,
  },
  ...over,
});

test("first upload: a single real V1 point and an informative summary", () => {
  const series = buildSeries([row(1)], "health");
  assert.equal(series.points.length, 1);
  assert.deepEqual(series.points.map((p) => [p.label, p.value]), [["V1", 65]]);
  assert.match(summarizeSeries(series), /V1: 65\.0 - the first recorded version/);
  assert.deepEqual(describeChanges(series), []);
});

test("second upload: V1 -> V2 uses the stored values and the real delta", () => {
  const series = buildSeries([row(1), row(2)], "health");
  assert.deepEqual(series.points.map((p) => p.value), [65, 70]);
  const [change] = describeChanges(series);
  assert.deepEqual([change.from, change.to, change.delta, change.direction], ["V1", "V2", 5, "improved"]);
  assert.match(summarizeSeries(series), /V1 → V2: 65\.0 → 70\.0 \(\+5\.0, improved\)/);
});

test("third upload: V1 -> V2 -> V3 keeps all three points in order", () => {
  const series = buildSeries([row(1), row(2), row(3)], "health");
  assert.deepEqual(series.points.map((p) => p.label), ["V1", "V2", "V3"]);
  assert.deepEqual(series.points.map((p) => p.value), [65, 70, 75]);
  assert.equal(describeChanges(series).length, 2);
});

test("every selectable metric reads its own stored field", () => {
  const history = [row(1), row(2), row(3)];
  const values = Object.fromEntries(
    TREND_METRICS.map((m) => [m.key, buildSeries(history, m.key).points.map((p) => p.value)]),
  );
  assert.deepEqual(values.health, [65, 70, 75]);
  assert.deepEqual(values.complexity, [3.5, 3, 2.5]);
  assert.deepEqual(values.maintainability, [71, 72, 73]);
  assert.deepEqual(values.duplication, [9, 8, 7]);
  assert.deepEqual(values.risk, [32, 34, 36]);
  assert.deepEqual(TREND_METRICS.map((m) => m.key), ["health", "complexity", "maintainability", "duplication", "risk"]);
});

test("direction respects whether higher or lower is better", () => {
  const history = [row(1), row(2)];
  assert.equal(describeChanges(buildSeries(history, "complexity"))[0].direction, "improved"); // 3.5 -> 3.0 (lower is better)
  assert.equal(describeChanges(buildSeries(history, "risk"))[0].direction, "worse"); // 32 -> 34
  assert.equal(describeChanges(buildSeries(history, "duplication"))[0].direction, "improved");
  assert.equal(describeChanges(buildSeries([row(1), row(2, { health_score: 65 })], "health"))[0].direction, "unchanged");
});

test("a value that was never stored stays null - not 0, not interpolated", () => {
  const history = [row(1), row(2, { metrics: { average_complexity: null } }), row(3)];
  const series = buildSeries(history, "complexity");
  assert.deepEqual(series.points.map((p) => p.value), [3.5, null, 2.5]);
  // No change is invented across the gap.
  assert.deepEqual(describeChanges(series), []);
  assert.match(summarizeSeries(series), /Not enough recorded values/);
});

test("a metric with no data at all reports hasData=false", () => {
  const series = buildSeries([row(1, { metrics: {} }), row(2, { metrics: {} })], "duplication");
  assert.equal(series.hasData, false);
  assert.match(summarizeSeries(series), /was not recorded/);
});

test("a real 0.0 score is kept as 0, a missing one as null (category trends)", () => {
  const doc = buildSeriesForMetric([row(1), row(2)], categoryMetric("documentation", "Documentation"));
  assert.deepEqual(doc.points.map((p) => p.value), [0, 0]);
  assert.equal(doc.hasData, true);
  const testing = buildSeriesForMetric([row(1), row(2)], categoryMetric("testing", "Testing"));
  assert.deepEqual(testing.points.map((p) => p.value), [null, null]);
  assert.equal(testing.hasData, false);
});

test("y-domain: fixed 0-100 for bounded metrics, data-driven for complexity", () => {
  const history = [row(1), row(2), row(3)];
  assert.deepEqual(yDomain(buildSeries(history, "health")), [0, 100]);
  const [lo, hi] = yDomain(buildSeries(history, "complexity"));
  assert.equal(lo, 0);
  assert.ok(hi >= 3.5);
  assert.deepEqual(yDomain(buildSeries([row(1, { metrics: {} })], "complexity")), [0, 1]);
});

test("versions fall back to position when version_number is absent", () => {
  const series = buildSeries([{ scan_id: 1, health_score: 50 }, { scan_id: 2, health_score: 55 }], "health");
  assert.deepEqual(series.points.map((p) => p.label), ["V1", "V2"]);
});

test("delta formatting", () => {
  const duplication = TREND_METRICS.find((m) => m.key === "duplication");
  assert.equal(formatDelta(-1.25, duplication), "-1.3%");
  assert.equal(formatDelta(2, duplication), "+2.0%");
  assert.equal(formatDelta(0, duplication), "0.0%");
});

test("empty history yields an empty series, never a crash", () => {
  const series = buildSeries([], "health");
  assert.equal(series.points.length, 0);
  assert.equal(series.hasData, false);
  assert.equal(summarizeSeries(series), "");
});
