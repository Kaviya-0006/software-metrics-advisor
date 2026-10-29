import { useState } from "react";
import { CATEGORY_LABELS, CATEGORY_ORDER } from "./CategoryScoresChart.jsx";
import {
  TREND_METRICS,
  buildSeries,
  buildSeriesForMetric,
  categoryMetric,
  describeChanges,
  formatDate,
  formatDelta,
  formatValue,
  summarizeSeries,
  yDomain,
} from "../utils/trendData.js";

const CHART_WIDTH = 560;
const CHART_HEIGHT = 200;
const PADDING = { top: 14, right: 18, bottom: 42, left: 46 };
const MAX_X_LABELS = 10;

/**
 * Hand-rolled SVG line chart over project versions (V1, V2, ...). Every point
 * is a stored value: a version that did not record the metric is a gap (the
 * line breaks, nothing is interpolated). A single version is drawn as one
 * centred point. The chart is responsive (viewBox + width 100%) and has a
 * text equivalent - an aria-label here and a value table below it.
 */
function TrendLineChart({ series, currentScanId }) {
  const { points, metric } = series;
  const [domainMin, domainMax] = yDomain(series);
  const innerWidth = CHART_WIDTH - PADDING.left - PADDING.right;
  const innerHeight = CHART_HEIGHT - PADDING.top - PADDING.bottom;

  const xFor = (index) =>
    points.length === 1
      ? PADDING.left + innerWidth / 2
      : PADDING.left + (innerWidth * index) / (points.length - 1);
  const yFor = (value) =>
    PADDING.top + innerHeight * (1 - (value - domainMin) / (domainMax - domainMin || 1));

  const found = points.findIndex((p) => p.scanId === currentScanId);
  const currentIndex = found >= 0 ? found : points.length - 1;

  const segments = [];
  let run = [];
  points.forEach((p, index) => {
    if (p.value == null) {
      if (run.length) segments.push(run);
      run = [];
    } else {
      run.push({ x: xFor(index), y: yFor(p.value) });
    }
  });
  if (run.length) segments.push(run);

  const ticks = [domainMin, (domainMin + domainMax) / 2, domainMax];
  const labelEvery = Math.max(1, Math.ceil(points.length / MAX_X_LABELS));
  const first = points[0];
  const last = points[points.length - 1];
  const ariaLabel =
    points.length === 1
      ? `${metric.label}: ${formatValue(first.value, metric)} for version ${first.label}`
      : `${metric.label} trend across ${points.length} versions, from ${first.label} (${formatValue(first.value, metric)}) to ${last.label} (${formatValue(last.value, metric)})`;

  return (
    <div className="trend-chart">
      <svg
        viewBox={`0 0 ${CHART_WIDTH} ${CHART_HEIGHT}`}
        role="img"
        aria-label={ariaLabel}
        className="trend-chart-svg"
      >
        {ticks.map((mark) => (
          <g key={mark}>
            <line
              x1={PADDING.left} x2={CHART_WIDTH - PADDING.right}
              y1={yFor(mark)} y2={yFor(mark)}
              className="trend-chart-gridline"
            />
            <text x={PADDING.left - 6} y={yFor(mark) + 3} textAnchor="end" className="trend-chart-axis-label">
              {Number.isInteger(mark) ? mark : mark.toFixed(1)}
              {metric.unit}
            </text>
          </g>
        ))}

        {segments.map((segment, i) => (
          <polyline
            key={i}
            className="trend-chart-line"
            points={segment.map((p) => `${p.x},${p.y}`).join(" ")}
          />
        ))}

        {points.map((p, index) =>
          p.value == null ? null : (
            <circle
              key={p.scanId ?? index}
              cx={xFor(index)}
              cy={yFor(p.value)}
              r={index === currentIndex ? "5.5" : "3.5"}
              className={`trend-chart-point ${index === currentIndex ? "trend-chart-point-current" : ""}`}
            >
              <title>
                {`${p.label} (${formatDate(p.date)}): ${formatValue(p.value, metric)}${index === currentIndex ? " - version shown below" : ""}`}
              </title>
            </circle>
          )
        )}

        {points.map((p, index) =>
          index % labelEvery === 0 || index === points.length - 1 ? (
            <g key={`x-${p.scanId ?? index}`}>
              <text x={xFor(index)} y={CHART_HEIGHT - 22} textAnchor="middle" className="trend-chart-axis-label">
                {p.label}
              </text>
              <text x={xFor(index)} y={CHART_HEIGHT - 8} textAnchor="middle" className="trend-chart-axis-label">
                {formatDate(p.date)}
              </text>
            </g>
          ) : null
        )}
      </svg>

      <table className="trend-table">
        <caption className="trend-table-caption">{metric.label} by version</caption>
        <thead>
          <tr>
            <th scope="col">Version</th>
            <th scope="col">Uploaded</th>
            <th scope="col">{metric.label}</th>
            <th scope="col">Change</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p, index) => {
            const previous = index > 0 ? points[index - 1] : null;
            let change = "-";
            if (previous && previous.value != null && p.value != null) {
              const delta = Math.round((p.value - previous.value) * 100) / 100;
              const direction =
                delta === 0 ? "unchanged" : (delta > 0) === metric.higherIsBetter ? "improved" : "worse";
              change = `${formatDelta(delta, metric)} (${direction})`;
            }
            return (
              <tr key={p.scanId ?? index} className={index === currentIndex ? "trend-table-current" : ""}>
                <th scope="row">
                  {p.label}
                  {index === currentIndex ? " (shown)" : ""}
                </th>
                <td>{formatDate(p.date)}</td>
                <td>{formatValue(p.value, metric)}</td>
                <td>{change}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

/**
 * Project trends across versions, from GET /api/projects/{id}/history.
 * Everything shown is a stored value of an immutable version; nothing is
 * recalculated, filled in or interpolated. `currentScanId` marks which version
 * the rest of the dashboard is showing.
 */
export default function HistoryTrends({ history, loading, error, onRetry, currentScanId }) {
  const [metricKey, setMetricKey] = useState(TREND_METRICS[0].key);
  const [selectedCategory, setSelectedCategory] = useState(CATEGORY_ORDER[0]);

  const rows = history || [];
  const series = rows.length ? buildSeries(rows, metricKey) : null;
  const categorySeries = rows.length
    ? buildSeriesForMetric(rows, categoryMetric(selectedCategory, CATEGORY_LABELS[selectedCategory]))
    : null;
  const hasBackfilled = rows.some((r) => r.is_backfilled);

  return (
    <section className="dashboard-panel">
      <h3>Project Trends</h3>

      {loading && <p className="loading-hint">Loading project history...</p>}

      {!loading && error && (
        <div className="error-hint">
          <p>Could not load project history: {error}</p>
          <button className="retry-button" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && history && rows.length === 0 && (
        <p className="empty-hint">No version history was found for this project.</p>
      )}

      {!loading && !error && rows.length > 0 && (
        <>
          {rows.length === 1 ? (
            <p className="empty-hint" role="status">
              This is version 1 - the first recorded scan of this project, so there is nothing to
              compare yet. To track changes, upload the next version and choose this project in the
              upload panel; the trend will then show V1 → V2.
            </p>
          ) : (
            <p className="trend-version-count">
              {rows.length} versions recorded (V{rows[0].version_number} → V{rows[rows.length - 1].version_number}).
            </p>
          )}

          <div className="trend-metric-picker" role="group" aria-label="Trend metric">
            {TREND_METRICS.map((m) => (
              <button
                key={m.key}
                type="button"
                className={`trend-metric-button ${m.key === metricKey ? "trend-metric-button-active" : ""}`}
                aria-pressed={m.key === metricKey}
                onClick={() => setMetricKey(m.key)}
                title={m.help}
              >
                {m.label}
              </button>
            ))}
          </div>

          <p className="trend-summary" aria-live="polite">
            {series.metric.help} {summarizeSeries(series)}
          </p>

          {series.hasData ? (
            <TrendLineChart series={series} currentScanId={currentScanId} />
          ) : (
            <p className="empty-hint">
              {series.metric.label} has no recorded values for these versions
              {rows.some((r) => r.is_backfilled) && series.metric.key === "duplication"
                ? " (project-level duplication was not stored before project versioning existed)"
                : ""}
              .
            </p>
          )}

          <div className="trend-category-picker">
            <label htmlFor="trend-category-select">Category trend:</label>
            <select
              id="trend-category-select"
              value={selectedCategory}
              onChange={(e) => setSelectedCategory(e.target.value)}
            >
              {CATEGORY_ORDER.map((key) => (
                <option key={key} value={key}>
                  {CATEGORY_LABELS[key]}
                </option>
              ))}
            </select>
          </div>

          {categorySeries.hasData ? (
            <TrendLineChart series={categorySeries} currentScanId={currentScanId} />
          ) : (
            <p className="empty-hint">
              {CATEGORY_LABELS[selectedCategory]} has no available data across these versions.
            </p>
          )}

          {hasBackfilled && (
            <p className="arch-hint">
              Versions created before project versioning existed were rebuilt from stored module data;
              values that were never stored for them are shown as N/A.
            </p>
          )}
        </>
      )}
    </section>
  );
}
