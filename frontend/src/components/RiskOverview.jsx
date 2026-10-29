const RISK_LEVELS = [
  { key: "low", label: "Low", color: "#16a34a" },
  { key: "medium", label: "Medium", color: "#d97706" },
  { key: "high", label: "High", color: "#ea580c" },
  { key: "critical", label: "Critical", color: "#dc2626" },
];

/**
 * Simple bar chart of module counts per risk level, from
 * GET /api/projects/{id}/risks' `risk_summary`. Bar heights are relative
 * to the largest count so the shape stays readable regardless of project
 * size.
 */
export default function RiskOverview({ riskSummary }) {
  if (!riskSummary) return null;

  const maxCount = Math.max(1, ...RISK_LEVELS.map((l) => riskSummary[l.key] ?? 0));
  const total = RISK_LEVELS.reduce((sum, l) => sum + (riskSummary[l.key] ?? 0), 0);

  return (
    <div>
      <div className="risk-overview">
        {RISK_LEVELS.map(({ key, label, color }) => {
          const count = riskSummary[key] ?? 0;
          const heightPct = (count / maxCount) * 100;
          const emphasize = (key === "high" || key === "critical") && count > 0;
          return (
            <div className="risk-bar-column" key={key}>
              <span className={`risk-bar-count ${emphasize ? "risk-bar-count-emphasis" : ""}`}>
                {count}
              </span>
              <div className="risk-bar-track">
                <div
                  className="risk-bar-fill"
                  style={{ height: `${count === 0 ? 2 : heightPct}%`, background: color }}
                />
              </div>
              <span className="risk-bar-label">{label}</span>
            </div>
          );
        })}
      </div>
      <p className="risk-overview-total">{total} module{total === 1 ? "" : "s"} risk-scored in total</p>
    </div>
  );
}
