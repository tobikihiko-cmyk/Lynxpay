type ValidationIssue = {
  loc?: unknown;
  msg?: unknown;
};

function issueMessage(issue: ValidationIssue): string | null {
  if (typeof issue.msg !== "string" || !issue.msg) return null;
  const location = Array.isArray(issue.loc)
    ? issue.loc.filter((part) => part !== "body").join(".")
    : "";
  return location ? `${location}: ${issue.msg}` : issue.msg;
}

export function apiErrorMessage(payload: unknown, fallback: string): string {
  if (!payload || typeof payload !== "object") return fallback;
  const detail = (payload as { detail?: unknown }).detail;
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => (item && typeof item === "object" ? issueMessage(item) : null))
      .filter(Boolean);
    return messages.length ? messages.join("; ") : fallback;
  }
  if (detail && typeof detail === "object") return issueMessage(detail) || fallback;
  return fallback;
}
