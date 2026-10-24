import type { HealthStatus, RiskLevel } from '../types/metrics.js';

/**
 * Score -> label bands, ported 1:1 from the Python backend
 * (RISK_LEVEL_BANDS in risk.py, HEALTH_STATUS_BANDS in scoring.py).
 * Typed tuples + `satisfies` make a misspelt label a compile error.
 */
const RISK_BANDS = [
  [0, 29, 'LOW'],
  [30, 59, 'MEDIUM'],
  [60, 79, 'HIGH'],
  [80, 100, 'CRITICAL'],
] as const satisfies readonly (readonly [number, number, RiskLevel])[];

const HEALTH_BANDS = [
  [0, 39, 'CRITICAL'],
  [40, 59, 'POOR'],
  [60, 74, 'NEEDS_ATTENTION'],
  [75, 89, 'HEALTHY'],
  [90, 100, 'EXCELLENT'],
] as const satisfies readonly (readonly [number, number, HealthStatus])[];

export function riskLevelForScore(score: number): RiskLevel {
  for (const [low, high, label] of RISK_BANDS) {
    if (score >= low && score <= high) return label;
  }
  return score > 100 ? 'CRITICAL' : 'LOW'; // defensive fallback, as in Python
}

export function healthStatusForScore(score: number): HealthStatus {
  for (const [low, high, label] of HEALTH_BANDS) {
    if (score >= low && score <= high) return label;
  }
  return score > 100 ? 'EXCELLENT' : 'CRITICAL';
}
