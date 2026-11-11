"use client";

import { useSyncExternalStore } from "react";
import { create } from "zustand";
import { createJSONStorage, persist } from "zustand/middleware";
import type { SessionUser } from "./types";

interface SessionState {
  token: string | null;
  user: SessionUser | null;
  signIn: (token: string, user: SessionUser) => void;
  signOut: () => void;
}

// The token lives in sessionStorage so it ends with the browser tab.
export const useSession = create<SessionState>()(
  persist(
    (set) => ({
      token: null,
      user: null,
      signIn: (token, user) => set({ token, user }),
      signOut: () => set({ token: null, user: null }),
    }),
    {
      name: "careflow-session",
      storage: createJSONStorage(() => sessionStorage),
      partialize: (s) => ({ token: s.token, user: s.user }),
    },
  ),
);

/** Whether the signed-in user has a permission (the API enforces it too). */
export function useCan(permission: string) {
  return useSession((s) => Boolean(s.user?.permissions?.includes(permission)));
}

/** True once the saved session has been read from sessionStorage. */
export function useHydrated() {
  return useSyncExternalStore(
    (onChange) => useSession.persist.onFinishHydration(onChange),
    () => useSession.persist.hasHydrated(),
    () => false,
  );
}
