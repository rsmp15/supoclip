import { createAuthClient } from "better-auth/react";
import { useSyncExternalStore } from "react";

export const authClient = createAuthClient();

export const { signIn, signOut, signUp, requestPasswordReset, resetPassword } = authClient;

const subscribeToHydration = () => () => {};
const clientSnapshot = () => true;
const serverSnapshot = () => false;

export const LOCAL_DEV_USER = {
  id: "local-user",
  name: "Local Creator",
  email: "creator@localhost",
  emailVerified: true,
  is_admin: true,
  createdAt: new Date().toISOString(),
  updatedAt: new Date().toISOString(),
};

export const LOCAL_DEV_SESSION = {
  user: LOCAL_DEV_USER,
  session: {
    id: "local-session",
    userId: "local-user",
    expiresAt: new Date(Date.now() + 1000 * 60 * 60 * 24 * 365).toISOString(),
  },
};

export function useSession(...args: Parameters<typeof authClient.useSession>) {
  const session = authClient.useSession(...args);
  const hydrated = useSyncExternalStore(subscribeToHydration, clientSnapshot, serverSnapshot);

  if (!hydrated) {
    return { ...session, data: null, isPending: true };
  }

  if (!session.data?.user && process.env.NEXT_PUBLIC_LOCAL_SINGLE_USER === "true") {
    return {
      data: LOCAL_DEV_SESSION as any,
      isPending: false,
      error: null,
      refetch: session.refetch,
    };
  }

  return session;
}
