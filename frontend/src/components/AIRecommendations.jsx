import { RiskLevelPill } from "./RiskyModulesTable.jsx";

/**
 * Displays AI-generated explanations/recommendations for the selected
 * project, from GET /api/projects/{id}/recommendations. This component
 * only renders what the backend returns — it never computes a severity,
 * priority, or recommendation itself, and never fabricates data for any
 * of its states.
 *
 * `data` is the raw {project_id, available, recommendations, message}
 * response (or null while nothing has loaded yet / after an error).
 */
export default function AIRecommendations({ data, loading, error, onRetry }) {
  return (
    <section className="dashboard-panel ai-recommendations">
      <h3>AI Recommendations</h3>
      <p className="ai-recommendations-subtitle">
        AI-generated guidance based on this project's software metrics and
        detected risks. Suggestions are advisory — review them before
        applying any change.
      </p>

      {loading && (
        <p className="loading-hint">Generating AI recommendations...</p>
      )}

      {!loading && error && (
        <div className="error-hint">
          <p>Could not load AI recommendations: {error}</p>
          <button className="retry-button" onClick={onRetry}>
            Retry
          </button>
        </div>
      )}

      {!loading && !error && data && !data.available && (!data.recommendations || data.recommendations.length === 0) && (
        <div className="ai-unavailable">
          <p className="ai-unavailable-title">AI recommendations unavailable</p>
          <p className="empty-hint">
            Start or configure the AI provider to generate recommendations.
            {data.message ? ` (${data.message})` : ""}
          </p>
        </div>
      )}

      {!loading && !error && data && (data.available || (data.recommendations && data.recommendations.length > 0)) && (
        <>
          {(!data.recommendations || data.recommendations.length === 0) && (
            <p className="empty-hint">
              No recommendations generated — the AI advisor found nothing
              significant to flag for this project.
            </p>
          )}

          {data.recommendations && data.recommendations.length > 0 && (
            <div className="recommendation-list">
              {data.recommendations.map((rec, index) => (
                <article className="recommendation-card" key={`${rec.problem}-${index}`}>
                  <div className="recommendation-card-header">
                    <RiskLevelPill level={rec.severity} />
                    <span className="recommendation-priority">
                      {rec.priority != null ? `Priority ${rec.priority}` : "Priority N/A"}
                    </span>
                  </div>

                  <h4 className="recommendation-problem">{rec.problem}</h4>
                  {rec.file_path && <p><strong>File:</strong> {rec.file_path}</p>}
                  {rec.official_risk && <p><strong>Official risk:</strong> {rec.official_risk} · <strong>AI concern:</strong> {rec.severity}</p>}
                  {rec.disagreement && <p role="note">{rec.disagreement}</p>}
                  {rec.evidence && Object.keys(rec.evidence).length > 0 && <details><summary>Measured evidence</summary><ul>{Object.entries(rec.evidence).map(([key,value]) => <li key={key}>{key}: {String(value)}</li>)}</ul></details>}
                  {rec.provenance && <p><small>{rec.provenance}</small></p>}

                  <div className="recommendation-section">
                    <span className="module-detail-label">Explanation</span>
                    <p>{rec.explanation}</p>
                  </div>

                  <div className="recommendation-section">
                    <span className="module-detail-label">Recommended action</span>
                    <p>{rec.recommendation}</p>
                  </div>
                </article>
              ))}
            </div>
          )}
        </>
      )}
    </section>
  );
}
