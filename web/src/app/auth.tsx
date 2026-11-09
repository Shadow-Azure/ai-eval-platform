import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Navigate, Outlet, useLocation } from "react-router-dom";
import {
  fetchMe,
  login as loginRequest,
  logout as logoutRequest,
  type AuthSession,
} from "../api/auth";
import { AUTH_UNAUTHORIZED_EVENT } from "../api/types";

type AuthStatus = "loading" | "authenticated" | "anonymous";

interface AuthContextValue {
  status: AuthStatus;
  session: AuthSession | null;
  login: (email: string, password: string, remember: boolean) => Promise<AuthSession>;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (value === null) {
    throw new Error("useAuth must be used inside AuthProvider");
  }
  return value;
}

export function safeNext(raw: string | null): string {
  if (!raw) {
    return "/overview";
  }
  let target = raw;
  try {
    target = decodeURIComponent(raw);
  } catch {
    return "/overview";
  }
  if (!target.startsWith("/") || target.startsWith("//") || target.startsWith("/login")) {
    return "/overview";
  }
  return target;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const queryClient = useQueryClient();
  const [status, setStatus] = useState<AuthStatus>("loading");
  const [session, setSession] = useState<AuthSession | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const current = await fetchMe();
        if (!cancelled) {
          setSession(current);
          setStatus("authenticated");
        }
      } catch {
        if (cancelled) return;
        // A 401 means anonymous; other failures still land on login so the
        // operator sees the connection error when submitting credentials.
        setSession(null);
        setStatus("anonymous");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    const onUnauthorized = () => {
      queryClient.clear();
      setSession(null);
      setStatus("anonymous");
    };
    window.addEventListener(AUTH_UNAUTHORIZED_EVENT, onUnauthorized);
    return () => window.removeEventListener(AUTH_UNAUTHORIZED_EVENT, onUnauthorized);
  }, [queryClient]);

  const login = useCallback(
    async (email: string, password: string, remember: boolean) => {
      const next = await loginRequest(email, password, remember);
      queryClient.clear();
      setSession(next);
      setStatus("authenticated");
      return next;
    },
    [queryClient],
  );

  const logout = useCallback(async () => {
    try {
      await logoutRequest();
    } finally {
      queryClient.clear();
      setSession(null);
      setStatus("anonymous");
    }
  }, [queryClient]);

  const value = useMemo(
    () => ({ status, session, login, logout }),
    [status, session, login, logout],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function RequireAuth() {
  const { status } = useAuth();
  const location = useLocation();

  if (status === "loading") {
    return (
      <main className="auth-loading" role="status" aria-live="polite">
        <div className="auth-loading-brand">
          <span className="brand-mark" aria-hidden="true">
            EP
          </span>
          <span>Eval Platform</span>
        </div>
        <p>正在验证登录状态…</p>
      </main>
    );
  }

  if (status === "anonymous") {
    const next = `${location.pathname}${location.search}`;
    return <Navigate to={`/login?next=${encodeURIComponent(next)}`} replace />;
  }

  return <Outlet />;
}
