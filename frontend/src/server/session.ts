import { headers } from "next/headers";
import { auth } from "@/lib/auth";

/** Single-user local install (no login). Never set this on a server other people can reach. */
export const LOCAL_SINGLE_USER = process.env.LOCAL_SINGLE_USER === "true";

export const LOCAL_DEV_SESSION = {
  user: {
    id: "local-user",
    name: "Local Creator",
    email: "creator@localhost",
    emailVerified: true,
    is_admin: true,
    createdAt: new Date(),
    updatedAt: new Date(),
  },
  session: {
    id: "local-session",
    userId: "local-user",
    expiresAt: new Date(Date.now() + 1000 * 60 * 60 * 24 * 365),
    createdAt: new Date(),
    updatedAt: new Date(),
  },
};

export async function getServerSession() {
  try {
    const session = await auth.api.getSession({ headers: await headers() });
    if (session?.user) {
      return session;
    }
  } catch (e) {
    // no session
  }
  return LOCAL_SINGLE_USER ? LOCAL_DEV_SESSION : null;
}
