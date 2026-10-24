import { ResponseContractError } from '../errors/AppError.js';
import type { ProjectListResponse, ProjectSummaryDto } from '../types/api.js';

/**
 * Runtime validation without type assertions.
 *
 * TypeScript types vanish at runtime, and our data originates in SQLite,
 * which is dynamically typed: a column declared FLOAT can still hold a
 * string written by another tool. These user-defined type guards are the
 * bridge: after one passes, the compiler genuinely knows the shape, with no
 * `as` casts anywhere.
 */

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value);

const isNullableNumber = (value: unknown): value is number | null =>
  value === null || (typeof value === 'number' && Number.isFinite(value));

const isNullableString = (value: unknown): value is string | null =>
  value === null || typeof value === 'string';

export function isProjectSummaryDto(value: unknown): value is ProjectSummaryDto {
  if (!isRecord(value)) return false;
  const languages = value['languages'];
  return (
    Number.isInteger(value['id']) &&
    typeof value['name'] === 'string' &&
    isNullableString(value['upload_date']) &&
    isNullableNumber(value['health_score']) &&
    isNullableString(value['health_status']) &&
    isNullableNumber(value['files']) &&
    Array.isArray(languages) &&
    languages.every((language) => typeof language === 'string')
  );
}

export function isProjectListResponse(value: unknown): value is ProjectListResponse {
  return isRecord(value) && Array.isArray(value['projects']) && value['projects'].every(isProjectSummaryDto);
}

/** Assertion form: throws a descriptive error naming the first offending item. */
export function assertProjectListResponse(value: unknown): asserts value is ProjectListResponse {
  if (isProjectListResponse(value)) return;
  if (!isRecord(value) || !Array.isArray(value['projects'])) {
    throw new ResponseContractError('Response is not { projects: [...] }.');
  }
  const badIndex = value['projects'].findIndex((item: unknown) => !isProjectSummaryDto(item));
  throw new ResponseContractError(`projects[${badIndex}] does not match ProjectSummaryDto.`);
}
