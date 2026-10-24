/**
 * Domain interfaces for a code-analysis report.
 *
 * These mirror the dicts the Python engine produced (ModuleResult.to_dict,
 * ProjectAnalysis.to_dict, ModuleRisk.to_dict, health bands in scoring.py),
 * but as explicit, strictly typed contracts. Domain objects are camelCase;
 * the snake_case JSON the React app consumes is defined separately in
 * types/api.ts and produced at the HTTP edge only.
 *
 * `null` is used where Python used None: "not computed / not available".
 * It is never "0", because 0 is a real measurement (see zero-vs-null tests
 * in the Python suite).
 */

// ---------------------------------------------------------------------------
// Closed vocabularies. The arrays are the single source of truth: the union
// types are derived from them, and runtime guards reuse the same arrays.
// ---------------------------------------------------------------------------

export const SUPPORTED_LANGUAGES = ['python', 'java', 'javascript'] as const;
export type SupportedLanguage = (typeof SUPPORTED_LANGUAGES)[number];

export const RISK_LEVELS = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL'] as const;
export type RiskLevel = (typeof RISK_LEVELS)[number];

export const HEALTH_STATUSES = ['CRITICAL', 'POOR', 'NEEDS_ATTENTION', 'HEALTHY', 'EXCELLENT'] as const;
export type HealthStatus = (typeof HEALTH_STATUSES)[number];

// ---------------------------------------------------------------------------
// Per-file metrics
// ---------------------------------------------------------------------------

/** Invariant: total === code + comments + blank. */
export interface LocMetrics {
  total: number;
  code: number;
  comments: number;
  blank: number;
}

export interface FunctionComplexity {
  name: string;
  /** McCabe: 1 + decision points. */
  complexity: number;
  ownerClass: string | null;
}

export interface ComplexityMetrics {
  total: number;
  average: number;
  maximum: number;
  functions: readonly FunctionComplexity[];
  /** tree-sitter recovered from a syntax error; counts may be partial. */
  hasSyntaxError: boolean;
}

/** Everything the Engine computes for one file. */
export interface ModuleMetrics {
  loc: LocMetrics;
  complexity: ComplexityMetrics;
  /** 0-100, higher is better; null when it could not be computed. */
  maintainabilityIndex: number | null;
}

export interface SourceFileRef {
  absolutePath: string;
  /** Path relative to the project root, as shown in the UI. */
  relativePath: string;
  language: SupportedLanguage;
}

/** The Engine never throws per file: failures come back as `error`. */
export interface ModuleAnalysisResult {
  file: SourceFileRef;
  metrics: ModuleMetrics | null;
  error: string | null;
}

// ---------------------------------------------------------------------------
// Risk and report shapes
// ---------------------------------------------------------------------------

export interface RiskAssessment {
  /** 0-100, higher is riskier; null when there is not enough signal. */
  score: number | null;
  level: RiskLevel | null;
  factors: readonly string[];
}

/** One analysed file inside a project report. */
export interface ModuleReport {
  fileName: string;
  filePath: string;
  language: SupportedLanguage;
  loc: LocMetrics;
  classes: number | null;
  functions: number;
  complexity: Pick<ComplexityMetrics, 'total' | 'average' | 'maximum'>;
  maintainabilityIndex: number | null;
  /** Not produced by the Engine yet (planned ports: coupling, cohesion, DIT, duplication). */
  coupling: number | null;
  cohesion: number | null;
  depthOfInheritance: number | null;
  duplicationPercentage: number | null;
  duplicatedLines: number | null;
  parseError: string | null;
  risk: RiskAssessment | null;
}

export interface ProjectHealth {
  score: number | null;
  status: HealthStatus | null;
}

/** Full analysis report for one scan. */
export interface ProjectAnalysisReport {
  projectName: string;
  languages: readonly SupportedLanguage[];
  filesAnalyzed: number;
  totalClasses: number | null;
  totalFunctions: number;
  totalLoc: number;
  totalComplexity: number;
  averageComplexity: number;
  averageMaintainability: number | null;
  filesWithErrors: number;
  health: ProjectHealth;
  modules: readonly ModuleReport[];
}

/** Point-in-time metrics row (see MetricSnapshot in prisma/schema.prisma). */
export interface MetricSnapshot {
  id: number;
  projectId: number;
  /** null = project-level rollup. */
  moduleId: number | null;
  /** ISO-8601 UTC. */
  capturedAt: string;
  locTotal: number | null;
  locCode: number | null;
  complexityTotal: number | null;
  complexityMaximum: number | null;
  maintainabilityIndex: number | null;
  riskScore: number | null;
  healthScore: number | null;
}
