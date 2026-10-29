/**
 * Row of small stat cards summarizing a project's health. Every value is
 * read directly from the backend response passed in via props — nothing
 * here is computed or estimated client-side.
 */
export default function SummaryCards({ health, summary, categoryScores, riskyModuleCount }) {
  const avgMaintainability = categoryScores?.maintainability;

  const cards = [
    { label: "Health Score", value: health?.score != null ? health.score.toFixed(1) : "--" },
    { label: "Health Status", value: health?.status ? health.status.replace("_", " ") : "--" },
    { label: "Files Analyzed", value: summary?.files ?? "--" },
    { label: "Classes", value: summary?.classes ?? "--" },
    { label: "Functions", value: summary?.functions ?? "--" },
    { label: "Total LOC", value: summary?.total_loc ?? "--" },
    { label: "Risky Modules", value: riskyModuleCount ?? "--" },
    {
      label: "Avg. Maintainability",
      value: avgMaintainability != null ? avgMaintainability.toFixed(1) : "N/A",
    },
  ];

  return (
    <div className="summary-cards">
      {cards.map((card) => (
        <div className="summary-card" key={card.label}>
          <div className="summary-card-value">{card.value}</div>
          <div className="summary-card-label">{card.label}</div>
        </div>
      ))}
    </div>
  );
}
