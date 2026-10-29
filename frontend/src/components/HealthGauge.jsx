const STATUS_COLORS = {
  CRITICAL: "#dc2626",
  POOR: "#ea580c",
  NEEDS_ATTENTION: "#d97706",
  HEALTHY: "#16a34a",
  EXCELLENT: "#059669",
};

const STATUS_LABELS = {
  CRITICAL: "Critical",
  POOR: "Poor",
  NEEDS_ATTENTION: "Needs Attention",
  HEALTHY: "Healthy",
  EXCELLENT: "Excellent",
};

/**
 * Circular gauge showing the Project Health Score (0-100) and its status
 * band. Plain SVG — no charting dependency. `score`/`status` come
 * straight from the backend (health.score / health.status); this
 * component never computes or guesses a value.
 */
export default function HealthGauge({ score, status }) {
  const radius = 70;
  const circumference = 2 * Math.PI * radius;
  const clamped = Math.max(0, Math.min(100, score ?? 0));
  const offset = circumference * (1 - clamped / 100);
  const color = STATUS_COLORS[status] || "#6b7280";

  return (
    <div className="health-gauge">
      <svg
        width="180" height="180" viewBox="0 0 180 180"
        aria-label={`Project Health Score: ${Math.round(clamped)} out of 100, status ${status}`}
      >
        <circle
          cx="90" cy="90" r={radius}
          fill="none" stroke="#e5e7eb" strokeWidth="14"
        />
        <circle
          cx="90" cy="90" r={radius}
          fill="none" stroke={color} strokeWidth="14"
          strokeDasharray={circumference}
          strokeDashoffset={offset}
          strokeLinecap="round"
          transform="rotate(-90 90 90)"
        />
        <text x="90" y="86" textAnchor="middle" className="health-gauge-score">
          {score != null ? score.toFixed(1) : "--"}
        </text>
        <text x="90" y="108" textAnchor="middle" className="health-gauge-max">
          / 100
        </text>
      </svg>
      <div className="health-gauge-status" style={{ color }}>
        {STATUS_LABELS[status] || "Unknown"}
      </div>
    </div>
  );
}
