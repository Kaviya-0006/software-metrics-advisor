/**
 * Version comparison view helpers (Phase 9B) - pure functions, no React/DOM.
 *
 * They only format and filter what GET /api/identities/{id}/compare returned;
 * every number shown comes from that response (itself built from stored
 * values). Missing values stay "N/A" - never 0.
 */

/** Project-level metric cards, in display order. `key` indexes comparison.project.metrics. */
export const METRIC_CARDS = [
  { key: "health_score", label: "Health Score", digits: 1, unit: "" },
  { key: "average_complexity", label: "Complexity (avg)", digits: 2, unit: "" },
  { key: "average_maintainability", label: "Maintainability (avg)", digits: 1, unit: "" },
  { key: "duplication_percentage", label: "Duplication", digits: 1, unit: "%" },
  { key: "documentation_score", label: "Documentation score", digits: 1, unit: "" },
  { key: "total_loc", label: "Lines of code", digits: 0, unit: "" },
  { key: "files_analyzed", label: "Files", digits: 0, unit: "" },
  { key: "average_risk_score", label: "Risk score (avg)", digits: 1, unit: "" },
  { key: "high_risk_modules", label: "High + critical risk modules", digits: 0, unit: "" },
];

export const RISK_LEVEL_ORDER = ["LOW", "MEDIUM", "HIGH", "CRITICAL"];
export const STATUS_LABELS = {
  changed: "Changed",
  added: "Added",
  removed: "Removed",
  unchanged: "Unchanged (verified)",
  unverified: "Unverified",
};

function isNumber(value) {
  return typeof value === "number" && Number.isFinite(value);
}

export function formatNumber(value, digits = 1, unit = "") {
  if (!isNumber(value)) return "N/A";
  return `${value.toFixed(digits)}${unit}`;
}

export function formatSigned(value, digits = 1, unit = "") {
  if (!isNumber(value)) return "N/A";
  if (value === 0) return `0${unit}`;
  return `${value > 0 ? "+" : "-"}${Math.abs(value).toFixed(digits)}${unit}`;
}

/** Percent text for a metric_change object; says why when undefined. */
export function formatPercentChange(change) {
  if (!change || !isNumber(change.percent_change)) {
    if (change && change.direction === "unavailable") return "N/A";
    if (change && change.percent_note) return "n/a (previous is 0)";
    return "N/A";
  }
  if (change.percent_change === 0) return "0%";
  return `${formatSigned(change.percent_change, 1)}%`;
}

/** One metric card's texts + tone ("good" | "bad" | "neutral" | "na"). */
export function describeCard(config, change) {
  const unavailable = !change || change.direction === "unavailable";
  const tone =
    unavailable ? "na" : change.direction === "improved" ? "good" : change.direction === "regressed" ? "bad" : "neutral";
  const word = {
    improved: "Improved",
    regressed: "Regressed",
    unchanged: "Unchanged",
    changed: "Changed",
    unavailable: "Not available",
  }[change?.direction ?? "unavailable"];
  return {
    label: config.label,
    previous: formatNumber(change?.previous, config.digits, config.unit),
    current: formatNumber(change?.current, config.digits, config.unit),
    delta: unavailable ? "N/A" : formatSigned(change.absolute_change, config.digits, config.unit),
    percent: formatPercentChange(change),
    tone,
    word,
    significant: Boolean(change?.significant),
  };
}

/** Compact per-file metric cell: "10 → 30 (+20)" or "N/A". */
export function formatFileMetric(change, digits = 1) {
  if (!change || change.direction === "unavailable") return "N/A";
  if (change.direction === "unchanged") return formatNumber(change.current, digits);
  return `${formatNumber(change.previous, digits)} → ${formatNumber(change.current, digits)} (${formatSigned(change.absolute_change, digits)})`;
}

export function riskTransition(row) {
  const prev = row.previous_risk_level;
  const cur = row.current_risk_level;
  if (prev && cur) return prev === cur ? cur : `${prev} → ${cur}`;
  return cur || prev || "N/A";
}

/** The risk level used by the risk filter: current if the file still exists, else previous. */
export function effectiveRiskLevel(row) {
  return row.current_risk_level || row.previous_risk_level || null;
}

export function fileFilterOptions(items) {
  const languages = [...new Set(items.map((r) => r.language).filter(Boolean))].sort();
  const risks = RISK_LEVEL_ORDER.filter((level) => items.some((r) => effectiveRiskLevel(r) === level));
  const statuses = Object.keys(STATUS_LABELS).filter((s) => items.some((r) => r.status === s));
  return { languages, risks, statuses };
}

/**
 * Apply the dashboard filters. "ALL" (or empty) disables a filter;
 * `query` is a case-insensitive path substring.
 */
export function filterFiles(items, { language = "ALL", riskLevel = "ALL", status = "ALL", verdict = "ALL", query = "" } = {}) {
  const q = query.trim().toLowerCase();
  return items.filter((row) => {
    if (language !== "ALL" && row.language !== language) return false;
    if (riskLevel !== "ALL" && effectiveRiskLevel(row) !== riskLevel) return false;
    if (status !== "ALL" && row.status !== status) return false;
    if (verdict !== "ALL" && row.verdict !== verdict) return false;
    if (q && !row.path.toLowerCase().includes(q)) return false;
    return true;
  });
}

/** Default selector state: the API's resolved pair (latest vs predecessor). */
export function resolvedSelection(data) {
  if (!data?.available || !data.comparison) return { from: null, to: null };
  return { from: data.comparison.before.version_number, to: data.comparison.after.version_number };
}

export function versionOptionLabel(v) {
  const when = v.scanned_at ? new Date(v.scanned_at).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" }) : "";
  const health = isNumber(v.health_score) ? ` - health ${v.health_score.toFixed(1)}` : "";
  return `V${v.version_number}${when ? ` - ${when}` : ""}${health}`;
}

export function severityRank(severity) {
  return { high: 3, medium: 2, low: 1 }[severity] ?? 0;
}
