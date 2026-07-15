import { NextRequest, NextResponse } from "next/server";
import { backend, clearSession, requireSameOrigin, sessionTokens, setSession } from "@/lib/bff";

async function proxy(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  if (!["GET", "HEAD", "OPTIONS"].includes(request.method)) {
    try { await requireSameOrigin(); } catch { return NextResponse.json({ detail: "Origin rejected" }, { status: 403 }); }
  }
  const { path } = await context.params;
  if (path[0] === "auth" && ["login", "register", "refresh", "logout"].includes(path[1] || "")) {
    return NextResponse.json({ detail: "Use the secure session endpoint" }, { status: 404 });
  }
  const target = `/api/v1/${path.join("/")}${request.nextUrl.search}`;
  let tokens = await sessionTokens();
  const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer();
  const send = (access?: string) => backend(target, {
    method: request.method,
    headers: { "Content-Type": request.headers.get("content-type") || "application/json", ...(access ? { Authorization: `Bearer ${access}` } : {}) },
    body
  });
  let upstream = await send(tokens.access);
  let rotated: Record<string, unknown> | null = null;
  if (upstream.status === 401 && tokens.refresh) {
    const refresh = await backend("/api/v1/auth/refresh", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ refresh_token: tokens.refresh }) });
    if (refresh.ok) {
      const payload = await refresh.json() as Record<string, unknown>;
      rotated = payload;
      tokens = { access: String(payload.access_token), refresh: String(payload.refresh_token) };
      upstream = await send(tokens.access);
    }
  }
  const response = new NextResponse(upstream.body, { status: upstream.status, headers: {
    "Content-Type": upstream.headers.get("content-type") || "application/json",
    "Cache-Control": "no-store",
    ...(upstream.headers.get("x-request-id") ? { "X-Request-ID": upstream.headers.get("x-request-id")! } : {})
  } });
  if (rotated) setSession(response, rotated); else if (upstream.status === 401) clearSession(response);
  return response;
}

export const GET = proxy; export const POST = proxy; export const PATCH = proxy; export const DELETE = proxy;
