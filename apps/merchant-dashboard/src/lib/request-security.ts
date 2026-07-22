export type MutationHeaders = { origin: string | null; host: string | null; fetchSite: string | null };

export function mutationIsSameOrigin(input: MutationHeaders): boolean {
  if (input.fetchSite && !["same-origin", "none"].includes(input.fetchSite)) return false;
  if (!input.origin || !input.host) return false;
  try { return new URL(input.origin).host === input.host; } catch { return false; }
}
