/**
 * Wire format: exactly the JSON the existing React client already consumes
 * from the FastAPI backend (snake_case keys, `{ detail }` errors).
 * Keeping it byte-compatible means zero frontend changes.
 */

export interface ProjectSummaryDto {
  id: number;
  name: string;
  /** ISO-8601 UTC, e.g. "2026-10-23T10:15:00.000Z". */
  upload_date: string | null;
  health_score: number | null;
  health_status: string | null;
  files: number | null;
  languages: string[];
}

export interface ProjectListResponse {
  projects: ProjectSummaryDto[];
}

export interface ErrorResponse {
  detail: string;
}
