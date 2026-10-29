/**
 * Table of modules in the order the backend's risk ranking returned them
 * (already sorted descending by risk_score — this component does not
 * re-sort). Clicking a row selects that module for the detail panel.
 *
 * `totalModuleCount` (the project's full module count, before filtering
 * to risk-scored ones) lets the empty state distinguish "this project has
 * no modules at all" from "every module was analyzed but none carries a
 * risk score" — two different situations that deserve different messages.
 */
export default function RiskyModulesTable({ modules, totalModuleCount, selectedFileName, onSelectModule }) {
  if (!modules || modules.length === 0) {
    if (!totalModuleCount) {
      return <p className="empty-hint">No module information available for this project.</p>;
    }
    return <p className="empty-hint">No risky modules found — every module scored cleanly.</p>;
  }

  return (
    <div className="risk-table-wrap">
      <table className="risk-table">
        <thead>
          <tr>
            <th>Module</th>
            <th className="risk-table-num">Risk Score</th>
            <th>Risk Level</th>
            <th>Main Risk Factors</th>
          </tr>
        </thead>
        <tbody>
          {modules.map((module) => {
            const isSelected = module.file_name === selectedFileName;
            return (
              <tr
                key={module.file_path || module.file_name}
                className={`risk-table-row ${isSelected ? "risk-table-row-selected" : ""}`}
                onClick={() => onSelectModule(module.file_name)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" || e.key === " ") {
                    e.preventDefault();
                    onSelectModule(module.file_name);
                  }
                }}
                title={module.file_path || module.file_name}
                tabIndex={0}
                role="button"
                aria-pressed={isSelected}
                aria-label={`View details for ${module.file_name}, risk level ${module.risk_level || "unknown"}`}
              >
                <td className="risk-table-file">{module.file_name}</td>
                <td className="risk-table-num">{module.risk_score?.toFixed(1) ?? "--"}</td>
                <td>
                  <RiskLevelPill level={module.risk_level} />
                </td>
                <td className="risk-table-factors">
                  {module.risk_factors && module.risk_factors.length > 0
                    ? (
                      <div className="factor-chip-row">
                        {module.risk_factors.map((factor) => (
                          <span className="factor-chip" key={factor}>{factor}</span>
                        ))}
                      </div>
                    )
                    : <span className="risk-table-no-factors">No risk factors identified</span>}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

const LEVEL_COLORS = {
  LOW: "#16a34a",
  MEDIUM: "#d97706",
  HIGH: "#ea580c",
  CRITICAL: "#dc2626",
};

/**
 * Shared risk-level pill, used by both the table and ModuleDetailPanel so
 * the two never disagree visually. LOW/MEDIUM use a soft tinted
 * background; HIGH/CRITICAL switch to a solid fill for stronger emphasis
 * — deliberately just one extra step of intensity, not a rainbow of
 * colors, to keep the dashboard professional.
 */
export function RiskLevelPill({ level, score }) {
  const color = LEVEL_COLORS[level] || "#6b7280";
  const strong = level === "HIGH" || level === "CRITICAL";
  return (
    <span
      className={`risk-pill ${strong ? "risk-pill-strong" : ""}`}
      style={
        strong
          ? { background: color, color: "#fff" }
          : { background: `${color}1a`, color }
      }
    >
      {level || "N/A"}
      {score != null ? ` \u2014 ${score.toFixed(1)}` : ""}
    </span>
  );
}
