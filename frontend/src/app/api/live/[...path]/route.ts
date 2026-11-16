import { NextResponse } from "next/server";

import { createProxyResponse, fetchBackend } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

/** Signed proxy for the backend's /live/* routes (live stream clipping). */
async function proxyLiveRequest(
  request: Request,
  { params }: { params: Promise<{ path: string[] }> },
) {
  const session = await getServerSession();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }
  const { path } = await params;
  const incomingUrl = new URL(request.url);
  const targetPath = `/live/${path.map(encodeURIComponent).join("/")}${incomingUrl.search}`;
  const body = request.method === "GET" || request.method === "HEAD" ? undefined : await request.text();
  const forward: Record<string, string> = {};
  for (const name of ["content-type", "accept", "range", "if-range"]) {
    const value = request.headers.get(name);
    if (value && (name !== "content-type" || body)) forward[name] = value;
  }
  const upstream = await fetchBackend(targetPath, {
    method: request.method,
    signal: request.signal,
    userId: session.user.id,
    extraHeaders: forward,
    body,
    cache: "no-store",
  });
  return createProxyResponse(upstream);
}

export const GET = proxyLiveRequest;
export const POST = proxyLiveRequest;
