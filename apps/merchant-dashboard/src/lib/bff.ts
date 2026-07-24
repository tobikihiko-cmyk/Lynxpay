import { cookies, headers } from "next/headers";
import { NextResponse } from "next/server";
import { mutationIsSameOrigin } from "./request-security";

const configuredApi = process.env.LYNXPAY_API_URL || "api:8000";
const apiBase = configuredApi.includes("://") ? configuredApi : `http://${configuredApi}`;
const secure = process.env.LYNXPAY_COOKIE_SECURE
  ? process.env.LYNXPAY_COOKIE_SECURE === "true"
  : process.env.NODE_ENV === "production";
const cookieOptions = { httpOnly: true, secure, sameSite: "strict" as const, path: "/" };

export async function requireSameOrigin(): Promise<void> {
  const incoming = await headers();
  const origin = incoming.get("origin");
  const host = incoming.get("host");
  const fetchSite = incoming.get("sec-fetch-site");
  if (!mutationIsSameOrigin({ origin, host, fetchSite })) throw new Error("Cross-origin mutation rejected");
}

export function setSession(response: NextResponse, payload: Record<string, unknown>) {
  response.cookies.set("lp_access", String(payload.access_token), { ...cookieOptions, maxAge: Number(payload.expires_in) });
  response.cookies.set("lp_refresh", String(payload.refresh_token), { ...cookieOptions, maxAge: Number(payload.refresh_expires_in) });
}

export function clearSession(response: NextResponse) {
  response.cookies.set("lp_access", "", { ...cookieOptions, maxAge: 0 });
  response.cookies.set("lp_refresh", "", { ...cookieOptions, maxAge: 0 });
}

export async function backend(path: string, init: RequestInit = {}) {
  return fetch(`${apiBase}${path}`, { ...init, cache: "no-store" });
}

export async function sessionTokens() {
  const jar = await cookies();
  return { access: jar.get("lp_access")?.value, refresh: jar.get("lp_refresh")?.value };
}
