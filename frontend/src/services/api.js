/**
 * API service.
 *
 * Phase 1 added the health check. Phase 2 added project upload/analysis.
 * Phase 5 adds the read endpoints from Phase 4 (list/detail/risks) that
 * the dashboard consumes. AI/report calls are added in later phases.
 */

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

async function parseJsonOrThrow(response) {
  let body;
  try {
    body = await response.json();
  } catch {
    body = null;
  }
  if (!response.ok) {
    const message = body?.detail || `Request failed with status ${response.status}`;
    throw new Error(message);
  }
  return body;
}

export async function checkBackendHealth() {
  const response = await fetch(`${API_BASE_URL}/api/health`);
  if (!response.ok) {
    throw new Error(`Health check failed with status ${response.status}`);
  }
  return response.json();
}

/**
 * Upload a project ZIP for scanning and metric analysis.
 * @param {File} file - a .zip File object from a file input.
 * @param {{identityId?: number, createNew?: boolean, projectName?: string}} [options]
 *   Phase 9A: add a version to an existing project (identityId), or force a
 *   new project (createNew, optionally named); omit both for automatic matching.
 * @returns {Promise<object>} the parsed JSON response from
 *   POST /api/projects/upload (see backend/app/api/upload.py).
 */
export async function uploadProject(file, options = {}) {
  const formData = new FormData();
  formData.append("file", file);
  // Phase 9A project identity. Nothing is sent unless the user chose it, so a
  // plain upload behaves exactly like before (server-side automatic matching).
  if (options.identityId != null) formData.append("identity_id", String(options.identityId));
  if (options.createNew) formData.append("create_new", "true");
  if (options.projectName && options.projectName.trim()) {
    formData.append("project_name", options.projectName.trim());
  }

  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/upload`, {
      method: "POST",
      body: formData,
    });
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/**
 * POST /api/projects/suggest-match — which existing projects this ZIP may be
 * a new version of (file names + structure only; nothing is saved).
 */
export async function suggestMatch(file) {
  const formData = new FormData();
  formData.append("file", file);
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/suggest-match`, {
      method: "POST",
      body: formData,
    });
  } catch {
    throw new Error("Could not reach the server.");
  }
  return parseJsonOrThrow(response);
}

/** GET /api/identities — one entry per project (stable identity) with its latest version. */
export async function getIdentities() {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/identities`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/** DELETE /api/identities/{id} — permanently delete a project and all saved versions. */
export async function deleteIdentity(identityId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/identities/${identityId}`, {
      method: "DELETE",
    });
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  if (!response.ok) {
    let body = null;
    try { body = await response.json(); } catch { /* empty 204/invalid body */ }
    throw new Error(body?.detail || `Request failed with status ${response.status}`);
  }
}

/** GET /api/projects — every analyzed project, most recent first. */
export async function getProjects() {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/** GET /api/projects/{id} — full detail for one project. */
export async function getProjectDetail(projectId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/${projectId}`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/** GET /api/projects/{id}/metrics — module-level raw metrics only. */
export async function getProjectMetrics(projectId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/metrics`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/** GET /api/projects/{id}/risks — risk-level counts + risky-module ranking. */
export async function getProjectRisks(projectId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/risks`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/**
 * GET /api/projects/{id}/recommendations — AI-generated explanations and
 * recommendations. The browser never talks to the AI provider directly;
 * this always goes through our own backend, which is the only thing that
 * holds any provider configuration/credentials. The response's shape is
 * always {project_id, available, recommendations, message} — `available:
 * false` is a normal, expected response (not an HTTP error) when no AI
 * provider is configured or the provider is unreachable.
 */
export async function getProjectRecommendations(projectId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/recommendations`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/**
 * GET /api/projects/{id}/history — chronological scan history for the
 * logical project this scan belongs to (every past upload that shares
 * this project's exact name). Response shape:
 * {project_id, project_name, history: [{scan_id, scanned_at, health_score,
 * health_status, category_scores}, ...]}, oldest first.
 */
export async function getProjectHistory(projectId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/history`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/**
 * GET /api/projects/{id}/report — downloads the PDF Project Health
 * Report and triggers the browser's normal save/download flow. Unlike
 * every other function here, the response is a binary PDF, not JSON, so
 * errors are read from the body as text (FastAPI still returns a JSON
 * `{"detail": ...}` for 404s, but a raw fetch() error path has no JSON
 * to parse) and success is a side effect (the download), not a returned
 * value.
 */
export async function downloadProjectReport(projectId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/report`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }

  if (!response.ok) {
    let detail = `Report generation failed with status ${response.status}`;
    try {
      const body = await response.json();
      if (body?.detail) detail = body.detail;
    } catch {
      // response wasn't JSON -- keep the generic message above
    }
    throw new Error(detail);
  }

  const disposition = response.headers.get("content-disposition") || "";
  const filenameMatch = disposition.match(/filename="?([^"]+)"?/);
  const filename = filenameMatch ? filenameMatch[1] : `software-health-report-${projectId}.pdf`;

  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

/**
 * GET /api/projects/{id}/architecture — the Architecture Intelligence result
 * saved for this scan (Phase 8D). Response: {project_id, scan_id,
 * project_name, available, scanned_at, status, message, architecture}.
 * `available: false` (older scans, or a failed architecture step) is a normal
 * 200 response with an explanatory `message`, not an error.
 */
export async function getProjectArchitecture(projectId) {
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/projects/${projectId}/architecture`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}

/**
 * GET /api/identities/{id}/compare — compare two saved versions of one project
 * (Phase 9B). Without versions it compares the latest with its predecessor.
 * Response: {identity_id, project_name, available, reason, swapped, versions,
 * comparison}; `available: false` means the project has a single version.
 */
export async function getVersionComparison(identityId, fromVersion = null, toVersion = null) {
  const params = new URLSearchParams();
  if (fromVersion != null) params.set("from", String(fromVersion));
  if (toVersion != null) params.set("to", String(toVersion));
  const query = params.toString();
  let response;
  try {
    response = await fetch(`${API_BASE_URL}/api/identities/${identityId}/compare${query ? `?${query}` : ""}`);
  } catch {
    throw new Error("Could not reach the server. Check your connection and that the backend is running.");
  }
  return parseJsonOrThrow(response);
}
