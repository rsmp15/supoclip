import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function POST(request: Request) {
  let session = await getServerSession();
  if (!session?.user?.id) {
    try {
      session = await auth.api.getSession({ headers: await headers() });
    } catch {
      // ignore
    }
  }
  const userId = session?.user?.id || (process.env.LOCAL_SINGLE_USER === "true" ? "local-user" : "");
  if (!userId) {
    return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
  }

  const payload = await request.text();
  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const idempotencyKey = request.headers.get("idempotency-key");
  const upstream = await fetch(`${apiUrl}/tasks/`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...buildBackendAuthHeaders(userId),
      ...(await backendTraceHeaders()),
      ...(idempotencyKey ? { "idempotency-key": idempotencyKey } : {}),
    },
    body: payload,
  });

  const responseText = await upstream.text();
  const traceId = upstream.headers.get("x-trace-id");
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
      ...(traceId ? { "x-trace-id": traceId } : {}),
    },
  });
}
