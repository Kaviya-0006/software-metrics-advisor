import { useEffect, useState } from "react";
import { suggestMatch, uploadProject } from "../services/api.js";

/**
 * Upload interface. Owns all upload state/logic itself — Dashboard.jsx
 * does not duplicate any of this. On a successful upload, calls
 * `onUploadSuccess(projectId, identityId)` so the parent can refresh the
 * project list and switch to viewing the newly saved version.
 *
 * Phase 9A: the user can let the server decide ("auto": it only attaches the
 * upload to an existing project when name AND structure match), create a new
 * project, or explicitly add a version to an existing project. After a file is
 * chosen the server's match suggestions (names + structure only, nothing saved)
 * are shown so the choice is informed.
 */
export default function UploadPanel({ onUploadSuccess, identities = [] }) {
  const [selectedFile, setSelectedFile] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | uploading | success | error
  const [result, setResult] = useState(null);
  const [errorMessage, setErrorMessage] = useState("");

  const [mode, setMode] = useState("auto"); // auto | new | existing
  const [identityId, setIdentityId] = useState("");
  const [projectName, setProjectName] = useState("");
  const [suggestion, setSuggestion] = useState(null);

  useEffect(() => {
    // Look up match suggestions for the chosen file (best effort: a failure
    // simply means no suggestions are shown).
    if (!selectedFile) {
      setSuggestion(null);
      return undefined;
    }
    let cancelled = false;
    suggestMatch(selectedFile)
      .then((body) => {
        if (!cancelled) setSuggestion(body);
      })
      .catch(() => {
        if (!cancelled) setSuggestion(null);
      });
    return () => {
      cancelled = true;
    };
  }, [selectedFile]);

  function handleFileChange(event) {
    setSelectedFile(event.target.files?.[0] ?? null);
    setStatus("idle");
    setResult(null);
    setErrorMessage("");
  }

  async function handleAnalyze() {
    if (status === "uploading") return; // guard against duplicate submissions
    if (!selectedFile) {
      setErrorMessage("Please choose a .zip file first.");
      setStatus("error");
      return;
    }
    if (mode === "existing" && !identityId) {
      setErrorMessage("Choose which existing project this is a new version of.");
      setStatus("error");
      return;
    }
    setStatus("uploading");
    setErrorMessage("");

    try {
      const options =
        mode === "existing"
          ? { identityId: Number(identityId) }
          : mode === "new"
            ? { createNew: true, projectName }
            : {};
      const response = await uploadProject(selectedFile, options);
      setResult(response);
      setStatus("success");
      onUploadSuccess?.(response.project_id, response.version?.identity_id);
    } catch (err) {
      setErrorMessage(err.message || "Upload failed.");
      setStatus("error");
    }
  }

  const version = result?.version;

  return (
    <section className="upload-panel">
      <h2>Upload Project</h2>

      <div className="upload-controls">
        <label className="upload-file-label">
          <input
            type="file"
            accept=".zip"
            onChange={handleFileChange}
            disabled={status === "uploading"}
          />
          <span>{selectedFile ? selectedFile.name : "Choose a .zip file..."}</span>
        </label>
        <button
          onClick={handleAnalyze}
          disabled={!selectedFile || status === "uploading"}
        >
          {status === "uploading" ? "Analyzing..." : "Analyze Project"}
        </button>
      </div>

      <fieldset className="upload-options" disabled={status === "uploading"}>
        <legend className="arch-muted">Which project is this?</legend>
        <div className="upload-option">
          <label>
            <input type="radio" name="upload-mode" value="auto" checked={mode === "auto"} onChange={() => setMode("auto")} />{" "}
            Detect automatically (only matches when name and structure agree)
          </label>
        </div>
        <div className="upload-option">
          <label>
            <input type="radio" name="upload-mode" value="new" checked={mode === "new"} onChange={() => setMode("new")} />{" "}
            New project
          </label>
          {mode === "new" && (
            <input
              type="text"
              aria-label="New project name"
              placeholder="Project name (optional, defaults to the ZIP name)"
              value={projectName}
              maxLength={120}
              onChange={(e) => setProjectName(e.target.value)}
            />
          )}
        </div>
        <div className="upload-option">
          <label>
            <input
              type="radio"
              name="upload-mode"
              value="existing"
              checked={mode === "existing"}
              onChange={() => setMode("existing")}
              disabled={identities.length === 0}
            />{" "}
            New version of an existing project
          </label>
          {mode === "existing" && (
            <select
              aria-label="Existing project"
              value={identityId}
              onChange={(e) => setIdentityId(e.target.value)}
            >
              <option value="">Select a project...</option>
              {identities.map((i) => (
                <option key={i.identity_id} value={i.identity_id}>
                  {i.name} (v{i.latest_version}, {i.version_count} version{i.version_count === 1 ? "" : "s"})
                </option>
              ))}
            </select>
          )}
        </div>

        {suggestion && selectedFile && (
          <div>
            {suggestion.suggestions.length === 0 ? (
              <p className="arch-hint">No existing project looks like this ZIP; it would be saved as a new project.</p>
            ) : (
              <>
                <p className="arch-hint">
                  {suggestion.auto_match_identity_id != null
                    ? "Automatic detection would add this as a new version of:"
                    : "Possible matches (nothing is merged unless you choose it):"}
                </p>
                <ul className="upload-suggestions">
                  {suggestion.suggestions.map((s) => (
                    <li key={s.identity_id}>
                      <strong>{s.name}</strong> (v{s.latest_version}) — {s.confidence} confidence: {s.reasons.join("; ")}.{" "}
                      <button
                        type="button"
                        className="retry-button"
                        onClick={() => {
                          setMode("existing");
                          setIdentityId(String(s.identity_id));
                        }}
                      >
                        Add as new version
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
      </fieldset>

      {status === "uploading" && (
        <p className="upload-loading">Scanning source files and calculating metrics...</p>
      )}

      {status === "error" && (
        <p className="upload-error">Upload failed: {errorMessage}</p>
      )}

      {status === "success" && result && (
        <div className="upload-result">
          <p className="upload-success-line">
            "{result.project_name}" analyzed successfully — opening its dashboard below.
          </p>
          {version && (
            <p className="upload-version-note">
              {version.is_new_project
                ? `Saved as a new project (version 1).`
                : `Saved as version ${version.version_number} of "${result.project_name}".`}{" "}
              {version.reason}
            </p>
          )}
          {version?.warnings?.map((w) => (
            <p key={w} className="upload-warning" role="alert">
              {w}
            </p>
          ))}
          <ul>
            <li>Files analyzed: {result.analysis.project.files}</li>
            <li>Classes: {result.analysis.project.classes}</li>
            <li>Functions: {result.analysis.project.functions}</li>
            <li>Languages: {result.analysis.project.languages.join(", ")}</li>
            <li>Health score: {result.health.score.toFixed(1)} ({result.health.status.replace("_", " ")})</li>
          </ul>
        </div>
      )}
    </section>
  );
}
