import { useEffect, useState } from "react";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

export default function QualityInsights({ projectId }) {
  const [gate, setGate] = useState(null);
  const [languages, setLanguages] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let active = true;
    setGate(null); setLanguages(null); setError("");
    if (projectId == null) return;
    Promise.all([
      fetch(`${API_BASE_URL}/api/projects/${projectId}/quality-gate`).then(r => { if (!r.ok) throw Error(`Quality gate HTTP ${r.status}`); return r.json(); }),
      fetch(`${API_BASE_URL}/api/projects/${projectId}/language-health`).then(r => { if (!r.ok) throw Error(`Language health HTTP ${r.status}`); return r.json(); }),
    ]).then(([g,l]) => { if (active) {setGate(g); setLanguages(l);} })
      .catch(e => {if (active) setError(e.message);});
    return () => { active = false; };
  }, [projectId]);
  return <section className="dashboard-panel">
    <h3>Quality Gate &amp; Language Intelligence</h3>
    {error && <p role="alert">{error}</p>}
    {!error && !gate && <p>Loading stored quality results…</p>}
    {gate && <div>
      <h4>Quality Gate: {gate.status}</h4>
      <p>{gate.note}</p>
      <ul>{gate.rules.map(r => <li key={r.name}>{r.passed === true ? "✓" : r.passed === false ? "✕" : "—"} {r.name}: {r.value ?? "Not measured"}</li>)}</ul>
    </div>}
    {languages && <div>
      <h4>Language Breakdown</h4>
      <div className="language-health-grid">
        {languages.languages.map(l => {
          const mi = l.mean_maintainability;
          const complex = l.mean_complexity;
          const criticalRisk = (l.risk_counts.HIGH || 0) + (l.risk_counts.CRITICAL || 0);
          const miWidth = mi == null ? 0 : Math.max(0, Math.min(100, mi));
          return (
            <div className="language-health-card" key={l.language}>
              <div className="language-health-header"><strong>{l.language}</strong><span>{l.files} file{l.files === 1 ? "" : "s"}</span></div>
              <div className="language-health-metric"><span>Maintainability</span><strong>{mi ?? "N/A"}</strong></div>
              <div className="language-health-bar"><span style={{width: `${miWidth}%`}} /></div>
              <div className="language-health-stats">
                <span>LOC: {l.loc}</span>
                <span>Complexity: {complex ?? "N/A"}</span>
                <span>High/Critical: {criticalRisk}</span>
              </div>
            </div>
          );
        })}
      </div>
      <div style={{overflowX:"auto", marginTop: 12}}><table style={{width:"100%",textAlign:"left"}}><thead><tr><th>Language</th><th>Files</th><th>LOC</th><th>Mean complexity</th><th>Mean MI</th><th>High/Critical</th></tr></thead><tbody>
      {languages.languages.map(l => <tr key={`row-${l.language}`}><td>{l.language}</td><td>{l.files}</td><td>{l.loc}</td><td>{l.mean_complexity ?? "—"}</td><td>{l.mean_maintainability ?? "—"}</td><td>{(l.risk_counts.HIGH || 0) + (l.risk_counts.CRITICAL || 0)}</td></tr>)}
      </tbody></table></div><p>{languages.note}</p>
    </div>}
  </section>;
}
