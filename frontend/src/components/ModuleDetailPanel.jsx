import { RiskLevelPill } from "./RiskyModulesTable.jsx";

function formatValue(value, suffix = "") {
  if (value === null || value === undefined) return "N/A";
  return `${value}${suffix}`;
}

/**
 * Full metric + risk detail for one module. `module` is expected to be
 * the merge of one entry from GET /api/projects/{id} (or /metrics) with
 * its matching entry from GET /api/projects/{id}/risks — done once in
 * Dashboard.jsx, not recomputed here. Every field is displayed as
 * returned; unavailable metrics show "N/A", never 0. Genuine zero values
 * (e.g. 0 coupling, 0% duplication) pass straight through unchanged,
 * since `formatValue` only special-cases null/undefined.
 */
export default function ModuleDetailPanel({ module }) {
  if (!module) {
    return (
      <p className="empty-hint">No module selected — choose one from the table above to see its full metrics.</p>
    );
  }

  const rows = [
    ["Language", module.language],
    ["Total LOC", module.loc?.total],
    ["Code Lines", module.loc?.code],
    ["Comment Lines", module.loc?.comments],
    ["Blank Lines", module.loc?.blank],
    ["Classes", module.classes],
    ["Functions", module.functions],
    ["Complexity (total)", module.complexity?.total],
    ["Complexity (average)", module.complexity?.average],
    ["Complexity (max)", module.complexity?.maximum],
    ["Maintainability Index", module.maintainability_index],
    ["Coupling", module.coupling],
    ["Cohesion (LCOM1)", module.cohesion],
    ["Depth of Inheritance", module.depth_of_inheritance],
    ["Duplication %", module.duplication_percentage, "%"],
    ["Test Coverage", module.test_coverage],
    ["Documentation %", module.documentation_percentage, "%"],
  ];

  return (
    <div className="module-detail-panel">
      <div className="module-detail-header">
        <h3 className="module-detail-filename" title={module.file_name}>{module.file_name}</h3>
        <span className="module-detail-path" title={module.file_path}>{module.file_path}</span>
        {module.risk_level && <RiskLevelPill level={module.risk_level} score={module.risk_score} />}
      </div>

      {module.parse_error && (
        <p className="module-parse-error">This file could not be parsed: {module.parse_error}</p>
      )}

      <div className="module-detail-grid">
        {rows.map(([label, value, suffix]) => (
          <div className="module-detail-cell" key={label}>
            <span className="module-detail-label">{label}</span>
            <span className="module-detail-value">{formatValue(value, suffix)}</span>
          </div>
        ))}
      </div>

      <div className="module-detail-factors">
        <span className="module-detail-label">Risk Factors</span>
        {module.risk_factors && module.risk_factors.length > 0 ? (
          <div className="factor-chip-row">
            {module.risk_factors.map((factor) => (
              <span className="factor-chip" key={factor}>{factor}</span>
            ))}
          </div>
        ) : (
          <p className="empty-hint module-detail-no-factors">No risk factors identified.</p>
        )}
      </div>
    </div>
  );
}
