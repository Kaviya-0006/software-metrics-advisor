export const CATEGORY_LABELS = {
  maintainability: "Maintainability",
  complexity: "Complexity",
  code_quality: "Code Quality",
  testing: "Testing",
  coupling_cohesion: "Coupling / Cohesion",
  duplication: "Duplication",
  documentation: "Documentation",
  code_churn: "Code Churn",
};

export const CATEGORY_ORDER = [
  "maintainability", "complexity", "code_quality", "testing",
  "coupling_cohesion", "duplication", "documentation", "code_churn",
];

function barColor(value) {
  if (value >= 75) return "#16a34a";
  if (value >= 60) return "#65a30d";
  if (value >= 40) return "#d97706";
  return "#dc2626";
}

/**
 * Horizontal bar chart of the Phase 3 category scores. A category the
 * backend reports as `null` (currently Code Churn) is rendered as an
 * explicit "N/A" row with no bar, never as 0 or 100. Testing is now a
 * static test-file presence score and therefore has a real value when source
 * files are available.
 */
export default function CategoryScoresChart({ categoryScores }) {
  if (!categoryScores) return null;

  return (
    <div className="category-chart">
      {CATEGORY_ORDER.map((key) => {
        const value = categoryScores[key];
        const label = CATEGORY_LABELS[key];
        const isAvailable = value !== null && value !== undefined;

        return (
          <div className="category-row" key={key}>
            <span className="category-label">{label}</span>
            <div className="category-bar-track">
              {isAvailable ? (
                <div
                  className="category-bar-fill"
                  style={{ width: `${value}%`, background: barColor(value) }}
                />
              ) : (
                <div className="category-bar-unavailable" />
              )}
            </div>
            <span className={`category-value ${isAvailable ? "" : "category-value-na"}`}>
              {isAvailable ? value.toFixed(1) : "N/A"}
            </span>
          </div>
        );
      })}
    </div>
  );
}
