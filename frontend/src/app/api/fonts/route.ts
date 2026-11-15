import { headers } from "next/headers";
import { NextResponse } from "next/server";

import { auth } from "@/lib/auth";
import { buildBackendAuthHeaders } from "@/lib/backend-auth";
import { backendTraceHeaders } from "@/server/backend-api";
import { getServerSession } from "@/server/session";

export async function GET() {
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

  const apiUrl =
    process.env.BACKEND_INTERNAL_URL ||
    process.env.NEXT_PUBLIC_API_URL ||
    "http://localhost:8000";
  const normalizedApiUrl = apiUrl.replace(/\/$/, "");
  const backendAuthHeaders = { ...buildBackendAuthHeaders(userId), ...(await backendTraceHeaders()) };

  let upstream = await fetch(`${normalizedApiUrl}/fonts`, {
    headers: {
      ...backendAuthHeaders,
    },
    cache: "no-store",
  });

  if (upstream.status === 404) {
    upstream = await fetch(`${normalizedApiUrl}/api/fonts`, {
      headers: {
        ...backendAuthHeaders,
      },
      cache: "no-store",
    });
  }

  const responseText = await upstream.text();
  return new NextResponse(responseText, {
    status: upstream.status,
    headers: {
      "Content-Type": upstream.headers.get("content-type") || "application/json",
    },
  });
}
