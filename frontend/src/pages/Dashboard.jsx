import { useEffect, useRef, useState } from "react";
import {
  checkBackendHealth,
  downloadProjectReport,
  getProjectArchitecture,
  getProjectDetail,
  getProjectHistory,
  getProjectRecommendations,
  getProjectRisks,
  getIdentities,
  deleteIdentity,
} from "../services/api.js";
import UploadPanel from "../components/UploadPanel.jsx";
import HealthGauge from "../components/HealthGauge.jsx";
import SummaryCards from "../components/SummaryCards.jsx";
import CategoryScoresChart from "../components/CategoryScoresChart.jsx";
import RiskOverview from "../components/RiskOverview.jsx";
import RiskyModulesTable from "../components/RiskyModulesTable.jsx";
import ModuleDetailPanel from "../components/ModuleDetailPanel.jsx";
import AIRecommendations from "../components/AIRecommendations.jsx";
import QualityInsights from "../components/QualityInsights.jsx";
import HistoryTrends from "../components/HistoryTrends.jsx";
import VersionComparison from "../components/VersionComparison.jsx";
import ArchitectureIntelligence from "../components/ArchitectureIntelligence.jsx";
import { exportDashboardSVG } from "../utils/svgExport.js";

function formatDate(isoString) {
  if (!isoString) return "";
  try {
    return new Date(isoString).toLocaleDateString(undefined, {
      year: "numeric", month: "short", day: "numeric",
    });
  } catch {
    return "";
  }
}

/**
 * Dashboard page.
 *
 * Owns the project list + selected-project data flow:
 *   GET /api/identities          -> project list (one entry per project, latest version), most recent selected by default
 *   GET /api/projects/{id}       -> health, category scores, summary, raw per-module metrics
 *   GET /api/projects/{id}/risks -> risk-level counts + risk_score/risk_level/risk_factors per module
 *
 * The two per-project responses are merged client-side by file_name so
 * the table/detail panel can show raw metrics and risk info together —
 * no metric or risk score is ever computed here, only combined for display.
 */
export default function Dashboard() {
  const [backendStatus, setBackendStatus] = useState("Checking...");

  const [projects, setProjects] = useState([]);
  const [projectsLoading, setProjectsLoading] = useState(true);
  const [projectsError, setProjectsError] = useState("");

  const [selectedProjectId, setSelectedProjectId] = useState(null); // the scan/version being viewed
  const [selectedIdentityId, setSelectedIdentityId] = useState(null); // the stable project it belongs to
  const [expandedIdentityId, setExpandedIdentityId] = useState(null); // UI-only expanded project card
  const [deleteTarget, setDeleteTarget] = useState(null);
  const [deletingProject, setDeletingProject] = useState(false);
  const [deleteError, setDeleteError] = useState("");
  const [detail, setDetail] = useState(null);
  const [risks, setRisks] = useState(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [detailError, setDetailError] = useState("");

  const [selectedFileName, setSelectedFileName] = useState(null);
  const [detailRetryToken, setDetailRetryToken] = useState(0);

  const [recommendations, setRecommendations] = useState(null);
  const [recommendationsLoading, setRecommendationsLoading] = useState(false);
  const [recommendationsError, setRecommendationsError] = useState("");
  const [recommendationsRetryToken, setRecommendationsRetryToken] = useState(0);

  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyError, setHistoryError] = useState("");
  const [historyRetryToken, setHistoryRetryToken] = useState(0);
  const [historyIdentityId, setHistoryIdentityId] = useState(null); // Phase 9B: project the history belongs to

  const [architecture, setArchitecture] = useState(null);
  const [architectureLoading, setArchitectureLoading] = useState(false);
  const [architectureError, setArchitectureError] = useState("");
  const [architectureRetryToken, setArchitectureRetryToken] = useState(0);

  const [reportDownloading, setReportDownloading] = useState(false);
  const [reportError, setReportError] = useState("");

  const [svgExporting, setSvgExporting] = useState(false);
  const [svgExportError, setSvgExportError] = useState("");
  const healthGaugeContainerRef = useRef(null);
  const historyContainerRef = useRef(null);

  useEffect(() => {
    checkBackendHealth()
      .then(() => setBackendStatus("Connected"))
      .catch(() => setBackendStatus("Offline"));
  }, []);

  async function loadProjects({ selectMostRecent } = {}) {
    setProjectsLoading(true);
    setProjectsError("");
    try {
      // Phase 9A: the picker lists projects (stable identities), each opening
      // at its latest version; older versions are chosen with the version selector.
      const body = await getIdentities();
      const entries = body.identities.map((i) => ({
        id: i.latest_scan_id,
        identity_id: i.identity_id,
        name: i.name,
        health_score: i.latest_health_score,
        health_status: i.latest_health_status,
        upload_date: i.updated_at,
        latest_version: i.latest_version,
        version_count: i.version_count,
      }));
      setProjects(entries);
      if (selectMostRecent && entries.length > 0) {
        setSelectedProjectId(entries[0].id);
        setSelectedIdentityId(entries[0].identity_id);
      }
      return entries;
    } catch (err) {
      setProjectsError(err.message || "Could not load projects.");
    } finally {
      setProjectsLoading(false);
    }
  }

  useEffect(() => {
    loadProjects({ selectMostRecent: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (selectedProjectId == null) {
      setDetail(null);
      setRisks(null);
      return;
    }

    let cancelled = false;
    setDetailLoading(true);
    setDetailError("");
    setSelectedFileName(null);
    // Defensively clear the previous project's data up front too, not
    // just gate it behind detailLoading — guarantees Project A's detail
    // can never be shown, even momentarily, while Project B is selected.
    setDetail(null);
    setRisks(null);
    // The PDF report is a one-shot download action rather than fetched
    // data, but its error/loading state is still per-project and must
    // not linger from a previously-selected project.
    setReportError("");
    setReportDownloading(false);
    setSvgExportError("");
    setSvgExporting(false);

    Promise.all([getProjectDetail(selectedProjectId), getProjectRisks(selectedProjectId)])
      .then(([detailBody, risksBody]) => {
        if (cancelled) return;
        setDetail(detailBody);
        setRisks(risksBody);
      })
      .catch((err) => {
        if (cancelled) return;
        setDetailError(err.message || "Could not load project analysis.");
      })
      .finally(() => {
        if (!cancelled) setDetailLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, detailRetryToken]);

  // Recommendations are fetched in their own effect, independent of the
  // detail/risks loading state above -- the health/risk dashboard must
  // stay usable while AI recommendations are still generating (or if the
  // AI provider is slow/unavailable), not freeze waiting on it.
  useEffect(() => {
    if (selectedProjectId == null) {
      setRecommendations(null);
      return;
    }

    let cancelled = false;
    setRecommendationsLoading(true);
    setRecommendationsError("");
    // Defensively clear immediately on project change too, same reasoning
    // as the detail/risks effect above -- Project A's recommendations must
    // never remain visible while Project B is selected, even momentarily.
    setRecommendations(null);

    getProjectRecommendations(selectedProjectId)
      .then((body) => {
        if (cancelled) return;
        setRecommendations(body);
      })
      .catch((err) => {
        if (cancelled) return;
        setRecommendationsError(err.message || "Could not load AI recommendations.");
      })
      .finally(() => {
        if (!cancelled) setRecommendationsLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, recommendationsRetryToken]);

  // History follows the same independent-effect, defensive-clear pattern
  // as recommendations above -- it must never block the rest of the
  // dashboard, and Project A's history must never remain visible while
  // Project B is selected.
  useEffect(() => {
    if (selectedProjectId == null) {
      setHistory(null);
      return;
    }

    let cancelled = false;
    setHistoryLoading(true);
    setHistoryError("");
    setHistory(null);

    getProjectHistory(selectedProjectId)
      .then((body) => {
        if (cancelled) return;
        setHistory(body.history);
        setHistoryIdentityId(body.identity_id ?? null);
      })
      .catch((err) => {
        if (cancelled) return;
        setHistoryError(err.message || "Could not load scan history.");
      })
      .finally(() => {
        if (!cancelled) setHistoryLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, historyRetryToken]);

  // Architecture Intelligence (Phase 8D): same independent-effect,
  // defensive-clear pattern as history/recommendations -- it never blocks the
  // rest of the dashboard, and Project A's architecture is never shown while
  // Project B is selected.
  useEffect(() => {
    if (selectedProjectId == null) {
      setArchitecture(null);
      return;
    }

    let cancelled = false;
    setArchitectureLoading(true);
    setArchitectureError("");
    setArchitecture(null);

    getProjectArchitecture(selectedProjectId)
      .then((body) => {
        if (cancelled) return;
        setArchitecture(body);
      })
      .catch((err) => {
        if (cancelled) return;
        setArchitectureError(err.message || "Could not load architecture analysis.");
      })
      .finally(() => {
        if (!cancelled) setArchitectureLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [selectedProjectId, architectureRetryToken]);

  async function handleDeleteProject() {
    if (!deleteTarget || deletingProject) return;

    setDeletingProject(true);
    setDeleteError("");
    try {
      const deletedIdentityId = deleteTarget.identity_id;
      const wasSelected = selectedIdentityId === deletedIdentityId;
      const entries = await deleteIdentity(deletedIdentityId).then(() => loadProjects());

      setExpandedIdentityId(null);
      setDeleteTarget(null);

      if (wasSelected) {
        const next = entries?.[0];
        setSelectedProjectId(next?.id ?? null);
        setSelectedIdentityId(next?.identity_id ?? null);
      } else if (selectedIdentityId && entries?.some((entry) => entry.identity_id === selectedIdentityId)) {
        // Keep the currently viewed project unchanged when a different project is deleted.
      } else {
        const next = entries?.[0];
        setSelectedProjectId(next?.id ?? null);
        setSelectedIdentityId(next?.identity_id ?? null);
      }
    } catch (err) {
      setDeleteError(err.message || "Could not delete this project.");
    } finally {
      setDeletingProject(false);
    }
  }

  function handleUploadSuccess(newProjectId, identityId) {
    loadProjects().then(() => {
      setSelectedProjectId(newProjectId);
      setSelectedIdentityId(identityId ?? null);
    });
  }

  async function handleDownloadReport() {
    if (reportDownloading || selectedProjectId == null) return; // guard against duplicate clicks
    setReportDownloading(true);
    setReportError("");
    try {
      await downloadProjectReport(selectedProjectId);
    } catch (err) {
      setReportError(err.message || "Could not generate the PDF report.");
    } finally {
      setReportDownloading(false);
    }
  }

  function handleExportSvg() {
    if (svgExporting || selectedProjectId == null) return; // guard against duplicate clicks
    setSvgExporting(true);
    setSvgExportError("");
    try {
      const sections = [];

      const gaugeSvg = healthGaugeContainerRef.current?.querySelector("svg");
      if (gaugeSvg) sections.push({ svg: gaugeSvg });

      // HistoryTrends renders zero, one, or two <svg> charts (Health Score
      // trend, plus the currently-selected category trend) depending on
      // whether enough scan history exists -- collect whichever are
      // actually present rather than assuming a fixed count.
      const historySvgs = historyContainerRef.current?.querySelectorAll("svg") || [];
      historySvgs.forEach((svg) => sections.push({ svg }));

      exportDashboardSVG(sections, `project-${selectedProjectId}-dashboard.svg`);
    } catch (err) {
      setSvgExportError(err.message || "Could not export the dashboard visualizations.");
    } finally {
      setSvgExporting(false);
    }
  }

  // Merge raw metrics (detail.modules) with risk info (risks.risky_modules)
  // by file_name. A module missing from the risk ranking (e.g. a parse
  // error, or nothing scorable) still appears, with risk fields left
  // undefined -- the components already render that as "N/A" / "--".
  const riskByFileName = new Map((risks?.risky_modules || []).map((r) => [r.file_name, r]));
  const mergedModules = (detail?.modules || []).map((m) => ({
    ...m,
    ...riskByFileName.get(m.file_name),
  }));
  const selectedModule = mergedModules.find((m) => m.file_name === selectedFileName) || null;

  return (
    <div className="page">
      <header className="hero">
        <h1>AI Software Metrics Advisor</h1>
        <p className="subtitle">Intelligent Project Health Monitoring</p>
        <p className="description">
          Analyze your software project, understand its quality, identify
          risky modules, and receive AI-powered recommendations.
        </p>
      </header>

      <section className="status-bar">
        <span>Backend Status:</span>
        <span className={`status-pill ${backendStatus === "Connected" ? "ok" : "down"}`}>
          {backendStatus}
        </span>
      </section>

      <UploadPanel onUploadSuccess={handleUploadSuccess} identities={projects} />

      <section className="project-picker">
        <h2>Analyzed Projects</h2>

        {projectsLoading && <p className="loading-hint">Loading projects...</p>}

        {!projectsLoading && projectsError && (
          <div className="error-hint">
            <p>Could not load projects: {projectsError}</p>
            <button className="retry-button" onClick={() => loadProjects({ selectMostRecent: true })}>
              Retry
            </button>
          </div>
        )}

        {!projectsLoading && !projectsError && projects.length === 0 && (
          <p className="empty-hint">
            No analyzed projects yet. Upload your first project above to begin.
          </p>
        )}

        {!projectsLoading && !projectsError && projects.length > 0 && (
          <div className="project-list">
            {projects.map((p) => {
              const isSelected = p.identity_id === selectedIdentityId;
              const isExpanded = p.identity_id === expandedIdentityId;
              const projectHistory = isSelected && historyIdentityId === p.identity_id ? history || [] : [];
              return (
                <article
                  key={p.identity_id}
                  className={`project-card ${isSelected ? "project-card-selected" : ""}`}
                >
                  <div className="project-card-top">
                  <button
                    type="button"
                    className="project-card-main"
                    onClick={() => {
                      setSelectedProjectId(p.id);
                      setSelectedIdentityId(p.identity_id);
                      setExpandedIdentityId((current) => current === p.identity_id ? null : p.identity_id);
                    }}
                    title={`Open ${p.name}`}
                    aria-current={isSelected ? "true" : undefined}
                    aria-expanded={isExpanded}
                  >
                    <span className="project-card-indicator" aria-hidden="true">{isExpanded ? "−" : "+"}</span>
                    <span className="project-card-identity">
                      <span className="project-card-name">{p.name}</span>
                      <span className="project-card-meta">
                        {p.version_count || 1} version{p.version_count === 1 ? "" : "s"} · Updated {formatDate(p.upload_date)}
                      </span>
                    </span>
                    <span className="project-card-version">V{p.latest_version}</span>
                    {p.health_status && (
                      <span className={`project-card-status status-${p.health_status.toLowerCase()}`}>
                        {p.health_status.replace("_", " ")}
                      </span>
                    )}
                    <span className="project-card-score">
                      {p.health_score != null ? p.health_score.toFixed(1) : "--"}
                    </span>
                  </button>
                  <button
                    type="button"
                    className="project-delete-button"
                    onClick={() => {
                      setDeleteError("");
                      setDeleteTarget({
                        identity_id: p.identity_id,
                        name: p.name,
                        version_count: p.version_count || 1,
                      });
                    }}
                    title={`Delete ${p.name}`}
                    aria-label={`Delete project ${p.name}`}
                  >
                    <svg viewBox="0 0 20 20" width="16" height="16" aria-hidden="true">
                      <path d="M7.5 3.5h5m-8 2h11m-7.5 0v9m4-9v9m-6.5-9 .65 10.2a1.5 1.5 0 0 0 1.5 1.4h4.7a1.5 1.5 0 0 0 1.5-1.4L15 5.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
                    </svg>
                  </button>
                  </div>

                  {isExpanded && (
                    <div className="project-versions" aria-label={`Versions of ${p.name}`}>
                      <div className="project-versions-header">
                        <span>Project versions</span>
                        <span>{p.version_count || 1} total</span>
                      </div>
                      {isSelected && historyLoading ? (
                        <div className="project-version-loading">Loading versions...</div>
                      ) : projectHistory.length > 0 ? (
                        <div className="project-version-list">
                          {[...projectHistory].reverse().map((version) => (
                            <button
                              type="button"
                              key={version.scan_id}
                              className={`project-version-row ${version.scan_id === selectedProjectId ? "project-version-row-selected" : ""}`}
                              onClick={() => {
                                setSelectedProjectId(version.scan_id);
                                setSelectedIdentityId(p.identity_id);
                              }}
                            >
                              <span className="project-version-number">V{version.version_number}</span>
                              <span className="project-version-date">{formatDate(version.scanned_at)}</span>
                              <span className={`project-card-status status-${(version.health_status || "").toLowerCase()}`}>
                                {(version.health_status || "N/A").replace("_", " ")}
                              </span>
                              <span className="project-version-score">
                                {version.health_score != null ? version.health_score.toFixed(1) : "--"}
                              </span>
                            </button>
                          ))}
                        </div>
                      ) : (
                        <div className="project-version-loading">Select this project to load its saved versions.</div>
                      )}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        )}
      </section>

      {selectedProjectId != null && (
        <section className="project-dashboard">
          {detailLoading && <p className="loading-hint">Loading project analysis...</p>}

          {!detailLoading && detailError && (
            <div className="error-hint">
              <p>Could not load this project: {detailError}</p>
              <button className="retry-button" onClick={() => setDetailRetryToken((n) => n + 1)}>
                Retry
              </button>
            </div>
          )}

          {!detailLoading && !detailError && detail && (
            <>
              <div className="project-dashboard-header">
                <h2 className="project-dashboard-title">{detail.project.name}</h2>
                {history && history.length > 1 && (
                  <div className="version-picker">
                    <label htmlFor="version-select">Version:</label>
                    <select
                      id="version-select"
                      className="version-select"
                      value={selectedProjectId}
                      onChange={(e) => setSelectedProjectId(Number(e.target.value))}
                    >
                      {[...history].reverse().map((h) => (
                        <option key={h.scan_id} value={h.scan_id}>
                          {`V${h.version_number} - ${formatDate(h.scanned_at)}${h.health_score != null ? ` - health ${h.health_score.toFixed(1)}` : ""}`}
                        </option>
                      ))}
                    </select>
                  </div>
                )}
                <button
                  className="report-download-button"
                  onClick={handleDownloadReport}
                  disabled={reportDownloading}
                  aria-label={`Download PDF health report for ${detail.project.name}`}
                >
                  {reportDownloading ? "Generating PDF..." : "Download PDF Report"}
                </button>
                <button
                  className="report-download-button svg-export-button"
                  onClick={handleExportSvg}
                  disabled={svgExporting}
                  aria-label={`Export dashboard visualizations for ${detail.project.name} as SVG`}
                >
                  <svg className="export-icon" viewBox="0 0 20 20" width="14" height="14" aria-hidden="true">
                    <path
                      d="M10 2v10m0 0-3.5-3.5M10 12l3.5-3.5M4 15v1.5A1.5 1.5 0 0 0 5.5 18h9a1.5 1.5 0 0 0 1.5-1.5V15"
                      fill="none" stroke="currentColor" strokeWidth="1.6"
                      strokeLinecap="round" strokeLinejoin="round"
                    />
                  </svg>
                  {svgExporting ? "Exporting SVG..." : "Export SVG"}
                </button>
              </div>

              {reportError && (
                <div className="error-hint">
                  <p>Could not generate the report: {reportError}</p>
                  <button className="retry-button" onClick={handleDownloadReport}>
                    Retry
                  </button>
                </div>
              )}

              {svgExportError && (
                <div className="error-hint">
                  <p>Could not export SVG: {svgExportError}</p>
                  <button className="retry-button" onClick={handleExportSvg}>
                    Retry
                  </button>
                </div>
              )}

              <div className="dashboard-top-row">
                <div ref={healthGaugeContainerRef}>
                  <HealthGauge score={detail.health.score} status={detail.health.status} />
                </div>
                <SummaryCards
                  health={detail.health}
                  summary={detail.summary}
                  categoryScores={detail.category_scores}
                  riskyModuleCount={detail.risky_modules.length}
                />
              </div>

              <div className="dashboard-mid-row">
                <div className="dashboard-panel">
                  <h3>Category Scores</h3>
                  <CategoryScoresChart categoryScores={detail.category_scores} />
                </div>

                <div className="dashboard-panel">
                  <h3>Risk Distribution</h3>
                  <RiskOverview riskSummary={risks?.risk_summary} />
                </div>
              </div>

              <div ref={historyContainerRef}>
                <HistoryTrends
                  history={history}
                  loading={historyLoading}
                  error={historyError}
                  onRetry={() => setHistoryRetryToken((n) => n + 1)}
                  currentScanId={selectedProjectId}
                />
              </div>

              <VersionComparison
                identityId={selectedIdentityId ?? historyIdentityId}
                versionCount={history ? history.length : 0}
              />

              <ArchitectureIntelligence
                data={architecture}
                loading={architectureLoading}
                error={architectureError}
                onRetry={() => setArchitectureRetryToken((n) => n + 1)}
              />

              <div className="dashboard-panel">
                <h3>Risky Modules</h3>
                <RiskyModulesTable
                  modules={mergedModules
                    .filter((m) => m.risk_score != null)
                    .sort((a, b) => b.risk_score - a.risk_score)}
                  totalModuleCount={mergedModules.length}
                  selectedFileName={selectedFileName}
                  onSelectModule={(fileName) =>
                    setSelectedFileName(fileName === selectedFileName ? null : fileName)
                  }
                />
              </div>

              <div className="dashboard-panel">
                <h3>Module Details</h3>
                <ModuleDetailPanel module={selectedModule} />
              </div>

              <QualityInsights projectId={selectedProjectId} />
              <AIRecommendations
                data={recommendations}
                loading={recommendationsLoading}
                error={recommendationsError}
                onRetry={() => setRecommendationsRetryToken((n) => n + 1)}
              />
            </>
          )}
        </section>
      )}

      {deleteTarget && (
        <div className="delete-modal-backdrop" role="presentation" onMouseDown={(event) => {
          if (event.target === event.currentTarget && !deletingProject) setDeleteTarget(null);
        }}>
          <section
            className="delete-modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="delete-project-title"
            onMouseDown={(event) => event.stopPropagation()}
          >
            <div className="delete-modal-icon" aria-hidden="true">
              <svg viewBox="0 0 20 20" width="20" height="20">
                <path d="M7.5 3.5h5m-8 2h11m-7.5 0v9m4-9v9m-6.5-9 .65 10.2a1.5 1.5 0 0 0 1.5 1.4h4.7a1.5 1.5 0 0 0 1.5-1.4L15 5.5" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/>
              </svg>
            </div>
            <div className="delete-modal-content">
              <h3 id="delete-project-title">Delete project?</h3>
              <p>Are you sure you want to permanently delete <strong>{deleteTarget.name}</strong>?</p>
              <p className="delete-modal-note">This removes the project and all {deleteTarget.version_count} saved version{deleteTarget.version_count === 1 ? "" : "s"}, including their stored analysis data.</p>
            </div>
            {deleteError && <p className="delete-modal-error">{deleteError}</p>}
            <div className="delete-modal-actions">
              <button type="button" className="delete-cancel-button" onClick={() => setDeleteTarget(null)} disabled={deletingProject}>Cancel</button>
              <button type="button" className="delete-confirm-button" onClick={handleDeleteProject} disabled={deletingProject}>
                {deletingProject ? "Deleting..." : "Delete project"}
              </button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}
