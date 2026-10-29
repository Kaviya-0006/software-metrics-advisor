import { useEffect, useMemo, useState } from "react";
import { getVersionComparison } from "../services/api.js";
import {
  METRIC_CARDS,
  RISK_LEVEL_ORDER,
  STATUS_LABELS,
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
} from "../utils/comparisonView.js";

const PAGE_SIZE = 50;
const ALERT_PREVIEW = 6;

function MetricCard({ config, change }) {
  const d = describeCard(config, change);
  return (
    <div className={`cmp-card cmp-card-${d.tone}`}>
      <div className="cmp-card-label">{d.label}</div>
      <div className="cmp-card-values" aria-label={`${d.label}: ${d.previous} before, ${d.current} after`}>
        <span className="cmp-card-prev">{d.previous}</span>
        <span aria-hidden="true"> → </span>
        <span className="cmp-card-cur">{d.current}</span>
      </div>
      <div className="cmp-card-delta">
        <strong>{d.delta}</strong> <span className="arch-muted">({d.percent})</span>
      </div>
      <div className="cmp-card-word">
        {d.word}
        {d.significant ? " · significant" : ""}
      </div>
    </div>
  );
}

function AlertList({ title, items, emptyText, tone }) {
  const [expanded, setExpanded] = useState(false);
  const shown = expanded ? items : items.slice(0, ALERT_PREVIEW);
  return (
    <div className={`cmp-alerts cmp-alerts-${tone}`}>
      <h4>
        {title} <span className="arch-count">{items.length}</span>
      </h4>
      {items.length === 0 ? (
        <p className="arch-muted">{emptyText}</p>
      ) : (
        <ul>
          {shown.map((a) => (
            <li key={a.id}>
              <span className={`cmp-sev cmp-sev-${a.severity}`}>{a.severity}</span> <strong>{a.title}</strong>
              <div className="arch-muted">{a.detail}</div>
              {a.evidence && a.evidence.length > 0 && (
                <div className="arch-muted">
                  {a.evidence.map((e, i) => (
                    <code key={i}>{e.line != null ? `${e.file}:${e.line}` : e.file} </code>
                  ))}
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
      {items.length > ALERT_PREVIEW && (
        <button type="button" className="retry-button" onClick={() => setExpanded((v) => !v)}>
          {expanded ? "Show fewer" : `Show all ${items.length}`}
        </button>
      )}
    </div>
  );
}

function ArchitectureComparison({ architecture }) {
  if (!architecture.available) {
    return <p className="empty-hint">{architecture.reason}</p>;
  }
  const rows = [
    ["Endpoints", architecture.endpoints],
    ["Frontend calls", architecture.calls],
    ["Matched calls", architecture.matched],
    ["Unmatched calls (potential issues)", architecture.unmatched_calls],
    ["Potentially unused endpoints", architecture.unused_endpoints],
  ];
  return (
    <>
      {architecture.project_type.changed && (
        <p>
          Project type changed: {architecture.project_type.previous} → {architecture.project_type.current}.
        </p>
      )}
      <div className="arch-table-wrap">
        <table className="arch-table">
          <caption className="trend-table-caption">API / integration findings between the two versions</caption>
          <thead>
            <tr>
              <th>Finding</th>
              <th>Before</th>
              <th>After</th>
              <th>New</th>
              <th>Gone</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([label, group]) => (
              <tr key={label}>
                <th scope="row">{label}</th>
                <td>{group.total.previous}</td>
                <td>{group.total.current}</td>
                <td>{group.added.length ? group.added.map((i) => i.key).join(", ") : "-"}</td>
                <td>{group.removed.length ? group.removed.map((i) => i.key).join(", ") : "-"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="arch-hint">{architecture.note}</p>
    </>
  );
}

/**
 * The comparison report itself (cards, alerts, uncertainty, file table with
 * filters, architecture). Presentational: all data comes from `comparison`.
 */
export function ComparisonReport({ comparison }) {
  const [filters, setFilters] = useState({ language: "ALL", riskLevel: "ALL", status: "ALL", verdict: "ALL", query: "" });
  const [visible, setVisible] = useState(PAGE_SIZE);
  const items = comparison.files.items;
  const options = useMemo(() => fileFilterOptions(items), [items]);
  const filtered = useMemo(() => filterFiles(items, filters), [items, filters]);
  const setFilter = (key) => (e) => {
    setFilters((f) => ({ ...f, [key]: e.target.value }));
    setVisible(PAGE_SIZE);
  };

  return (
    <>
          <p className="arch-hint">
            Comparing V{comparison.before.version_number} → V{comparison.after.version_number}. Every value is what
            those uploads stored; regressions and improvements use fixed thresholds (listed below).
          </p>

          <div className="cmp-cards">
            {METRIC_CARDS.map((config) => (
              <MetricCard key={config.key} config={config} change={comparison.project.metrics[config.key]} />
            ))}
          </div>

          <div className="arch-block">
            <h4>Risk distribution (modules per level)</h4>
            {comparison.project.risk_distribution.available ? (
              <div className="arch-table-wrap">
                <table className="arch-table">
                  <thead>
                    <tr>
                      <th>Level</th>
                      <th>Before</th>
                      <th>After</th>
                      <th>Change</th>
                    </tr>
                  </thead>
                  <tbody>
                    {RISK_LEVEL_ORDER.map((level) => {
                      const change = comparison.project.risk_distribution.levels[level.toLowerCase()];
                      return (
                        <tr key={level}>
                          <th scope="row">{level}</th>
                          <td>{formatNumber(change.previous, 0)}</td>
                          <td>{formatNumber(change.current, 0)}</td>
                          <td>{formatSigned(change.absolute_change, 0)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            ) : (
              <p className="arch-muted">Risk distribution was not stored for one of the versions.</p>
            )}
          </div>

          <div className="cmp-alert-grid">
            <AlertList
              title="Regression alerts"
              tone="bad"
              items={comparison.regressions}
              emptyText="No significant regressions under the published rules."
            />
            <AlertList
              title="Improvement highlights"
              tone="good"
              items={comparison.improvements}
              emptyText="No significant improvements under the published rules."
            />
          </div>

          {comparison.uncertainty.length > 0 && (
            <div className="arch-block cmp-uncertainty">
              <h4>
                Uncertainty and missing data <span className="arch-count">{comparison.uncertainty.length}</span>
              </h4>
              <ul>
                {comparison.uncertainty.map((u) => (
                  <li key={u}>{u}</li>
                ))}
              </ul>
            </div>
          )}

          <div className="arch-block">
            <h4>File-level comparison</h4>
            <p className="arch-muted">
              {comparison.files.summary.changed} changed · {comparison.files.summary.added} added ·{" "}
              {comparison.files.summary.removed} removed · {comparison.files.summary.unchanged} unchanged (verified) ·{" "}
              {comparison.files.summary.unverified} unverified
            </p>
            <div className="cmp-filters" role="group" aria-label="File filters">
              <label>
                Language{" "}
                <select value={filters.language} onChange={setFilter("language")}>
                  <option value="ALL">All</option>
                  {options.languages.map((l) => (
                    <option key={l} value={l}>
                      {l}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Risk level{" "}
                <select value={filters.riskLevel} onChange={setFilter("riskLevel")}>
                  <option value="ALL">All</option>
                  {options.risks.map((r) => (
                    <option key={r} value={r}>
                      {r}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Status{" "}
                <select value={filters.status} onChange={setFilter("status")}>
                  <option value="ALL">All</option>
                  {options.statuses.map((s) => (
                    <option key={s} value={s}>
                      {STATUS_LABELS[s]}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Verdict{" "}
                <select value={filters.verdict} onChange={setFilter("verdict")}>
                  <option value="ALL">All</option>
                  <option value="regressed">Regressed</option>
                  <option value="improved">Improved</option>
                  <option value="mixed">Mixed</option>
                  <option value="neutral">Neutral</option>
                </select>
              </label>
              <label>
                Path{" "}
                <input type="text" value={filters.query} onChange={setFilter("query")} placeholder="filter by path" />
              </label>
            </div>
            <p className="arch-muted" aria-live="polite">
              Showing {Math.min(visible, filtered.length)} of {filtered.length} matching files ({items.length} total).
            </p>

            {filtered.length === 0 ? (
              <p className="empty-hint">No files match these filters.</p>
            ) : (
              <div className="arch-table-wrap">
                <table className="arch-table cmp-file-table">
                  <caption className="trend-table-caption">Files compared between the two versions</caption>
                  <thead>
                    <tr>
                      <th>File</th>
                      <th>Status</th>
                      <th>Language</th>
                      <th>Risk</th>
                      <th>Complexity</th>
                      <th>Maintainability</th>
                      <th>Coupling</th>
                      <th>Cohesion</th>
                      <th>Duplication</th>
                      <th>Verdict</th>
                    </tr>
                  </thead>
                  <tbody>
                    {filtered.slice(0, visible).map((row) => (
                      <tr key={row.path} className={row.verdict ? `cmp-row-${row.verdict}` : ""}>
                        <td>
                          <code>{row.path}</code>
                          {row.notes.map((n) => (
                            <div key={n} className="arch-muted cmp-note">
                              {n}
                            </div>
                          ))}
                          {row.signals.map((s) => (
                            <div key={s.rule} className={`cmp-signal cmp-signal-${s.direction}`}>
                              {s.message}
                            </div>
                          ))}
                        </td>
                        <td>
                          <span className={`cmp-status cmp-status-${row.status}`}>{STATUS_LABELS[row.status]}</span>
                          {row.verification === "content-hash" && <div className="arch-muted">hash verified</div>}
                        </td>
                        <td>{row.language}</td>
                        <td>{riskTransition(row)}</td>
                        <td>{formatFileMetric(row.changes.complexity_total, 0)}</td>
                        <td>{formatFileMetric(row.changes.maintainability_index, 1)}</td>
                        <td>{formatFileMetric(row.changes.coupling, 0)}</td>
                        <td>{formatFileMetric(row.changes.cohesion, 1)}</td>
                        <td>{formatFileMetric(row.changes.duplication_percentage, 1)}</td>
                        <td>{row.verdict || "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {filtered.length > visible && (
              <button type="button" className="retry-button" onClick={() => setVisible((v) => v + PAGE_SIZE)}>
                Show more files
              </button>
            )}
          </div>

          <div className="arch-block">
            <h4>Architecture and API integration changes</h4>
            <ArchitectureComparison architecture={comparison.architecture} />
          </div>

          <details className="arch-block">
            <summary>How regressions and improvements are decided</summary>
            <ul className="arch-uncertain">
              {Object.entries(comparison.rules.project).map(([key, rule]) => (
                <li key={key}>
                  Project: {rule.label} - at least {rule.min_abs}
                  {rule.min_pct ? ` and ${rule.min_pct}%` : ""} change ({rule.higher_is_better ? "higher" : "lower"} is better)
                </li>
              ))}
              {comparison.rules.file.map((rule) => (
                <li key={rule.id}>
                  File: {rule.label} - at least {rule.min_abs}
                  {rule.min_pct ? ` and ${rule.min_pct}%` : ""} change ({rule.higher_is_better ? "higher" : "lower"} is better)
                </li>
              ))}
              <li>File: risk level moving between LOW / MEDIUM / HIGH / CRITICAL.</li>
              {comparison.rules.notes.map((n) => (
                <li key={n}>{n}</li>
              ))}
            </ul>
          </details>
    </>
  );
}

/**
 * Version Comparison dashboard section (Phase 9B).
 *
 * Presents GET /api/identities/{id}/compare: before/after metric cards,
 * regression alerts, improvement highlights, a filterable file-level table,
 * architecture/API changes and an explicit list of uncertainties. All values
 * come from the API response (built from stored data); nothing is computed or
 * filled in here beyond formatting and client-side filtering.
 */
export default function VersionComparison({ identityId, versionCount }) {
  const [selection, setSelection] = useState({ from: null, to: null }); // null = API default (latest vs predecessor)
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [retryToken, setRetryToken] = useState(0);

  // A different project, or a new version being added, goes back to the default pair.
  useEffect(() => {
    setSelection({ from: null, to: null });
  }, [identityId, versionCount]);

  useEffect(() => {
    if (identityId == null) {
      setData(null);
      return undefined;
    }
    let cancelled = false;
    setLoading(true);
    setError("");
    getVersionComparison(identityId, selection.from, selection.to)
      .then((body) => {
        if (!cancelled) {
          setData(body);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setData(null);
          setError(err.message || "Could not load the version comparison.");
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [identityId, selection.from, selection.to, retryToken, versionCount]);

  const comparison = data?.available ? data.comparison : null;
  const apiResolved = resolvedSelection(data);
  // The API resolves the default pair (latest vs predecessor). Once the
  // user changes a selector, keep that local choice instead of replacing it
  // with the API default on every render. Never send the same version as both
  // sides of a comparison.
  const resolved = {
    from: selection.from ?? apiResolved.from,
    to: selection.to ?? apiResolved.to,
  };

  function chooseBefore(value) {
    const from = Number(value);
    let to = resolved.to;
    if (from === to) {
      const alternative = data?.versions?.find((v) => v.version_number !== from);
      to = alternative?.version_number ?? null;
    }
    setSelection({ from, to });
  }

  function chooseAfter(value) {
    const to = Number(value);
    let from = resolved.from;
    if (to === from) {
      const alternative = data?.versions?.find((v) => v.version_number !== to);
      from = alternative?.version_number ?? null;
    }
    setSelection({ from, to });
  }
  return (
    <section className="dashboard-panel cmp-section" aria-labelledby="cmp-heading">
      <h3 id="cmp-heading">Version Comparison</h3>

      {loading && <p className="loading-hint">Loading version comparison...</p>}

      {!loading && error && (
        <div className="error-hint">
          <p>Could not load version comparison: {error}</p>
          <button className="retry-button" onClick={() => setRetryToken((n) => n + 1)}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && data && !data.available && (
        <p className="empty-hint" role="status">
          {data.reason}
        </p>
      )}

      {data && data.available && (
        <div className="cmp-selectors">
          <label>
            Before{" "}
            <select
              className="version-select"
              value={resolved.from ?? ""}
              onChange={(e) => chooseBefore(e.target.value)}
              aria-label="Version to compare from"
            >
              {data.versions.map((v) => (
                <option key={v.version_number} value={v.version_number}>
                  {versionOptionLabel(v)}
                </option>
              ))}
            </select>
          </label>
          <span aria-hidden="true">→</span>
          <label>
            After{" "}
            <select
              className="version-select"
              value={resolved.to ?? ""}
              onChange={(e) => chooseAfter(e.target.value)}
              aria-label="Version to compare to"
            >
              {data.versions.map((v) => (
                <option key={v.version_number} value={v.version_number}>
                  {versionOptionLabel(v)}
                </option>
              ))}
            </select>
          </label>
          {data.swapped && <span className="arch-muted">(shown oldest → newest)</span>}
        </div>
      )}

      {!loading && !error && comparison && <ComparisonReport key={`${comparison.before.version_number}-${comparison.after.version_number}`} comparison={comparison} />}
    </section>
  );
}
