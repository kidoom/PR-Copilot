export interface RepoToolScope {
  allowedFiles?: readonly string[]
  searchPathScopes?: readonly string[]
}

function normalizePath(value: string): string {
  return value.replace(/\\/g, '/').replace(/^\.?\//, '')
}

function isInsideScope(path: string, scope: string): boolean {
  const normalizedPath = normalizePath(path)
  const normalizedScope = normalizePath(scope).replace(/\/$/, '')
  return normalizedPath === normalizedScope || normalizedPath.startsWith(`${normalizedScope}/`)
}

export function isPathAllowedByScope(path: string, scope: RepoToolScope | null | undefined): boolean {
  if (!scope) return true

  const allowedFiles = scope.allowedFiles ?? []
  const searchPathScopes = scope.searchPathScopes ?? []
  if (allowedFiles.length === 0 && searchPathScopes.length === 0) return true

  return allowedFiles.some((file) => normalizePath(file) === normalizePath(path)) ||
    searchPathScopes.some((pathScope) => pathScope.length > 0 && isInsideScope(path, pathScope))
}

export function filterFilesByScope<T extends { filename: string }>(
  files: readonly T[],
  scope: RepoToolScope | null | undefined,
): T[] {
  if (!scope) return [...files]
  return files.filter((file) => isPathAllowedByScope(file.filename, scope))
}

