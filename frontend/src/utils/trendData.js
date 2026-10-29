/**
 * Trend data preparation (Phase 9A) - pure functions, no React, no DOM.
 *
 * Input is the `history` array of GET /api/projects/{id}/history: one entry per
 * immutable project version, oldest first. Output is exactly the stored values:
 * a metric a version did not record stays `null` (a gap in the chart and "N/A"
 * in the text), it is never turned into 0 or interpolated.
 */

export const TREND_METRICS = [
  {
    key: "health",
    label: "Health Score",
    unit: "",
    domain: [0, 100],
    higherIsBetter: true,
    get: (row) => row.health_score,
    help: "Overall project health, 0-100 (higher is better).",
  },
  {
    key: "complexity",
    label: "Complexity",
    unit: "",
    domain: null, // scale follows the data
    higherIsBetter: false,
    get: (row) => row.metrics?.average_complexity,
    help: "Average cyclomatic complexity per module (lower is better).",
  },
  {
    key: "maintainability",
    label: "Maintainability",
    unit: "",
    domain: [0, 100],
    higherIsBetter: true,
    get: (row) => row.metrics?.average_maintainability,
    help: "Average Maintainability Index, 0-100 (higher is better).",
  },
  {
    key: "duplication",
    label: "Duplication",
    unit: "%",
    domain: [0, 100],
    higherIsBetter: false,
    get: (row) => row.metrics?.duplication_percentage,
    help: "Share of code lines that are duplicated (lower is better).",
  },
  {
    key: "risk",
    label: "Risk",
    unit: "",
    domain: [0, 100],
    higherIsBetter: false,
    get: (row) => row.metrics?.average_risk_score,
    help: "Average module risk score, 0-100 (lower is better).",
  },
];

export function getMetric(key) {
  return TREND_METRICS.find((m) => m.key === key) || TREND_METRICS[0];
}

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function versionLabel(row, index) {
  return `V${row.version_number ?? index + 1}`;
}

export function formatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}

export function formatValue(value, metric) {
  if (!isNumber(value)) return "N/A";
  return `${value.toFixed(1)}${metric.unit}`;
}

/**
 * One series per metric: [{label, version, scanId, date, value}] in history
 * order. `value` is a finite number or null.
 */
export function buildSeries(history, metricKey) {
  return buildSeriesForMetric(history, getMetric(metricKey));
}

/** Y-axis domain: the metric's fixed domain, or a rounded-up range for the data. */
export function yDomain(series) {
  if (series.metric.domain) return series.metric.domain;
  const values = series.points.map((p) => p.value).filter(isNumber);
  if (values.length === 0) return [0, 1];
  const max = Math.max(...values);
  const step = max <= 5 ? 1 : max <= 20 ? 5 : 10;
  return [0, Math.max(step, Math.ceil((max * 1.1) / step) * step)];
}

/**
 * Consecutive changes between versions that both have a value, with a
 * better/worse verdict that respects the metric's direction.
 * Returns [{from, to, delta, direction: "improved"|"worse"|"unchanged"}].
 */
export function describeChanges(series) {
  const changes = [];
  const { points, metric } = series;
  for (let i = 1; i < points.length; i += 1) {
    const a = points[i - 1];
    const b = points[i];
    if (a.value == null || b.value == null) continue;
    const delta = Math.round((b.value - a.value) * 100) / 100;
    let direction = "unchanged";
    if (delta !== 0) direction = (delta > 0) === metric.higherIsBetter ? "improved" : "worse";
    changes.push({ from: a.label, to: b.label, fromValue: a.value, toValue: b.value, delta, direction });
  }
  return changes;
}

export function formatDelta(delta, metric) {
  const sign = delta > 0 ? "+" : delta < 0 ? "-" : "";
  return `${sign}${Math.abs(delta).toFixed(1)}${metric.unit}`;
}

/** Plain-language one-liner shown above the chart. */
export function summarizeSeries(series) {
  const { points, metric } = series;
  if (points.length === 0) return "";
  if (!series.hasData) {
    return `${metric.label} was not recorded for ${points.length === 1 ? "this version" : "these versions"}.`;
  }
  if (points.length === 1) {
    return `${points[0].label}: ${formatValue(points[0].value, metric)} - the first recorded version of this project.`;
  }
  const changes = describeChanges(series);
  if (changes.length === 0) return `Not enough recorded values to compare ${metric.label} between versions.`;
  return changes
    .map(
      (c) =>
        `${c.from} → ${c.to}: ${formatValue(c.fromValue, metric)} → ${formatValue(c.toValue, metric)} (${formatDelta(c.delta, metric)}, ${c.direction})`,
    )
    .join("; ");
}

/** A 0-100 category score (health sub-score) presented like a metric. */
export function categoryMetric(key, label) {
  return {
    key: `category:${key}`,
    label,
    unit: "",
    domain: [0, 100],
    higherIsBetter: true,
    get: (row) => row.category_scores?.[key],
    help: `${label} category score, 0-100 (higher is better).`,
  };
}

/** Same as buildSeries, for an arbitrary metric object (e.g. categoryMetric). */
export function buildSeriesForMetric(history, metric) {
  const points = (history || []).map((row, index) => {
    const raw = metric.get(row);
    return {
      label: versionLabel(row, index),
      version: row.version_number ?? index + 1,
      scanId: row.scan_id,
      date: row.scanned_at,
      value: isNumber(raw) ? raw : null,
    };
  });
  return { metric, points, hasData: points.some((p) => p.value != null) };
}
