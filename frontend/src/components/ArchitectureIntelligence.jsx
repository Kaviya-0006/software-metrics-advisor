import { useMemo, useState } from "react";
import { downloadSvgString, renderArchitectureSvg } from "../utils/architectureDiagram.js";

const PROJECT_TYPE_LABELS = {
  "full-stack": "Full-stack",
  "frontend-only": "Frontend-only",
  "backend-only": "Backend-only",
  undetermined: "Undetermined",
};

function ConfidenceBadge({ level }) {
  return <span className={`arch-conf arch-conf-${level || "low"}`}>{level || "low"}</span>;
}

function Evidence({ items }) {
  if (!items || items.length === 0) return <span className="arch-muted">—</span>;
  return (
    <ul className="arch-evidence">
      {items.map((e, i) => (
        <li key={`${e.file}:${e.line}:${i}`}>
          <span className="arch-evidence-side">{e.side}</span>{" "}
          <code>{e.line != null ? `${e.file}:${e.line}` : e.file}</code>
          {e.detail ? <span className="arch-muted"> — {e.detail}</span> : null}
        </li>
      ))}
    </ul>
  );
}

function FindingsTable({ title, hint, items, emptyText, columns }) {
  return (
    <div className="arch-block">
      <h4>
        {title} <span className="arch-count">{items.length}</span>
      </h4>
      {hint && <p className="arch-hint">{hint}</p>}
      {items.length === 0 ? (
        <p className="arch-muted">{emptyText}</p>
      ) : (
        <div className="arch-table-wrap">
          <table className="arch-table">
            <thead>
              <tr>
                {columns.map((c) => (
                  <th key={c.key}>{c.label}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((item, i) => (
                <tr key={`${item.method}-${item.path}-${i}`}>
                  {columns.map((c) => (
                    <td key={c.key}>{c.render(item)}</td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

const callCell = (item) => (
  <code>
    {item.method} {item.path}
  </code>
);
const reasonCell = (item) => <span className="arch-reason">{item.reason}</span>;
const confCell = (item) => <ConfidenceBadge level={item.confidence} />;
const evidenceCell = (item) => <Evidence items={item.evidence} />;

/**
 * Architecture Intelligence dashboard section (Phase 8D).
 *
 * Pure presentation of GET /api/projects/{id}/architecture: every number,
 * finding and diagram element comes from the saved scan result. Findings are
 * static-analysis observations — "potential" issues / "potentially" unused
 * endpoints — and the section says so.
 */
export default function ArchitectureIntelligence({ data, loading, error, onRetry }) {
  const [exportError, setExportError] = useState("");
  const architecture = data?.available ? data.architecture : null;

  const svgMarkup = useMemo(
    () => (architecture ? renderArchitectureSvg(architecture.diagram, { title: "Architecture diagram" }) : ""),
    [architecture],
  );

  function handleExport() {
    setExportError("");
    try {
      if (!svgMarkup) throw new Error("No architecture diagram is available to export.");
      downloadSvgString(svgMarkup, `project-${data.project_id}-architecture.svg`);
    } catch (err) {
      setExportError(err.message || "Could not export the architecture diagram.");
    }
  }

  return (
    <div className="dashboard-panel arch-section">
      <div className="arch-header">
        <h3>Architecture Intelligence</h3>
        {architecture && (
          <button className="report-download-button svg-export-button" onClick={handleExport}>
            Export diagram SVG
          </button>
        )}
      </div>

      {loading && <p className="loading-hint">Loading architecture analysis...</p>}

      {!loading && error && (
        <div className="error-hint">
          <p>Could not load architecture analysis: {error}</p>
          <button className="retry-button" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && data && !data.available && (
        <p className="empty-hint">{data.message || "Architecture analysis is not available for this scan."}</p>
      )}

      {exportError && (
        <div className="error-hint">
          <p>Could not export SVG: {exportError}</p>
        </div>
      )}

      {!loading && !error && architecture && (
        <>
          <p className="arch-summary">
            <strong>{PROJECT_TYPE_LABELS[architecture.project_type] || architecture.project_type}</strong> project
            · overall confidence <ConfidenceBadge level={architecture.confidence} />
            <span className="arch-muted"> — {architecture.project_type_basis}</span>
          </p>
          <p className="arch-hint">
            Static-analysis results from file locations, names and source patterns — not runtime facts.
          </p>

          <div className="arch-block">
            <h4>Detected layers</h4>
            <div className="arch-table-wrap">
              <table className="arch-table">
                <thead>
                  <tr>
                    <th>Layer</th>
                    <th>Files</th>
                    <th>LOC</th>
                    <th>Languages (files)</th>
                    <th>Confidence</th>
                  </tr>
                </thead>
                <tbody>
                  {architecture.layers.map((layer) => (
                    <tr key={layer.layer}>
                      <td>
                        <strong>{layer.layer}</strong>
                        {!layer.detected && <span className="arch-muted"> (no layer evidence)</span>}
                      </td>
                      <td>{layer.files}</td>
                      <td>{layer.loc}</td>
                      <td>
                        {Object.entries(layer.languages)
                          .map(([language, count]) => `${language} ${count}`)
                          .join(", ")}
                      </td>
                      <td>
                        <ConfidenceBadge level={layer.confidence} />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="arch-block">
            <h4>Architecture diagram</h4>
            <div
              className="arch-diagram"
              // Generated from backend results by renderArchitectureSvg, which XML-escapes every label.
              dangerouslySetInnerHTML={{ __html: svgMarkup }}
            />
            <p className="arch-hint">{architecture.diagram.note}</p>
          </div>

          <FindingsTable
            title="Matched frontend/backend API calls"
            items={architecture.api_mapping.matched_items}
            emptyText="No frontend API call was matched to a backend endpoint."
            columns={[
              { key: "call", label: "Call", render: callCell },
              { key: "quality", label: "Match", render: (i) => i.match_quality },
              { key: "conf", label: "Confidence", render: confCell },
              { key: "ev", label: "Evidence (file:line)", render: evidenceCell },
            ]}
          />

          <FindingsTable
            title="Potential integration issues"
            hint="Frontend calls for which no matching backend endpoint was found in the analyzed code."
            items={architecture.api_mapping.unmatched_frontend_calls_items}
            emptyText="No unmatched frontend calls were found."
            columns={[
              { key: "call", label: "Call", render: callCell },
              { key: "why", label: "Finding", render: reasonCell },
              { key: "conf", label: "Confidence", render: confCell },
              { key: "ev", label: "Evidence (file:line)", render: evidenceCell },
            ]}
          />

          <FindingsTable
            title="Potentially unused backend endpoints"
            hint="No frontend consumer was found; they may still be used by other clients."
            items={architecture.api_mapping.unused_backend_endpoints_items}
            emptyText="No potentially unused backend endpoints were found."
            columns={[
              { key: "call", label: "Endpoint", render: callCell },
              { key: "why", label: "Finding", render: reasonCell },
              { key: "conf", label: "Confidence", render: confCell },
              { key: "ev", label: "Evidence (file:line)", render: evidenceCell },
            ]}
          />

          <div className="arch-block">
            <h4>
              Uncertain findings <span className="arch-count">{architecture.uncertainties.length}</span>
            </h4>
            {architecture.uncertainties.length === 0 ? (
              <p className="arch-muted">Nothing was flagged as uncertain.</p>
            ) : (
              <ul className="arch-uncertain">
                {architecture.uncertainties.map((u, i) => (
                  <li key={`${u.kind}-${i}`}>
                    <ConfidenceBadge level={u.confidence} /> <span className="arch-evidence-side">{u.kind}</span>{" "}
                    {u.message}
                    <Evidence items={u.evidence} />
                  </li>
                ))}
              </ul>
            )}
          </div>

          <details className="arch-block">
            <summary>
              Layer classification evidence ({architecture.files.length} file
              {architecture.files.length === 1 ? "" : "s"})
            </summary>
            <div className="arch-table-wrap">
              <table className="arch-table">
                <thead>
                  <tr>
                    <th>File</th>
                    <th>Layer</th>
                    <th>Confidence</th>
                    <th>Evidence</th>
                  </tr>
                </thead>
                <tbody>
                  {architecture.files.map((f) => (
                    <tr key={f.path}>
                      <td>
                        <code>{f.path}</code>
                      </td>
                      <td>{f.layer}</td>
                      <td>
                        <ConfidenceBadge level={f.confidence} />
                      </td>
                      <td>{f.evidence.join("; ")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </details>
        </>
      )}
    </div>
  );
}
