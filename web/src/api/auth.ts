import { ApiError, notifyUnauthorized, type EntityRef } from "./types";

export type AuthRole = "admin" | "viewer";

export interface AuthUser {
  id: string;
  email: string;
}

export interface AuthSession {
  user: AuthUser;
  tenant: EntityRef;
  workspace: EntityRef | null;
  role: AuthRole;
  roleLabel: string;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function readEntity(value: unknown, field: string): EntityRef {
  if (!isRecord(value) || typeof value.slug !== "string" || typeof value.name !== "string") {
    throw new ApiError(`认证响应缺少 ${field} 字段`, { code: "invalid_response" });
  }
  return { slug: value.slug, name: value.name };
}

export function parseAuthSession(payload: unknown): AuthSession {
  if (!isRecord(payload) || !isRecord(payload.user)) {
    throw new ApiError("控制面返回了无法识别的认证信息", { code: "invalid_response" });
  }
  const user = payload.user;
  if (typeof user.id !== "string" || typeof user.email !== "string") {
    throw new ApiError("认证响应缺少用户字段", { code: "invalid_response" });
  }
  if (payload.role !== "admin" && payload.role !== "viewer") {
    throw new ApiError("认证响应中的角色无效", { code: "invalid_response" });
  }
  return {
    user: { id: user.id, email: user.email },
    tenant: readEntity(payload.tenant, "tenant"),
    workspace:
      payload.workspace === null || payload.workspace === undefined
        ? null
        : readEntity(payload.workspace, "workspace"),
    role: payload.role,
    roleLabel: typeof payload.role_label === "string" ? payload.role_label : payload.role,
  };
}

async function readPayload(response: Response): Promise<unknown> {
  const contentType = response.headers.get("content-type") ?? "";
  if (!contentType.includes("application/json")) {
    return null;
  }
  try {
    return (await response.json()) as unknown;
  } catch {
    return null;
  }
}

function errorInfo(payload: unknown): { code: string | null; message: string | null } {
  if (!isRecord(payload)) return { code: null, message: null };
  const detail = payload.detail;
  const error = isRecord(detail) && isRecord(detail.error) ? detail.error : null;
  if (!error) return { code: null, message: null };
  return {
    code: typeof error.code === "string" ? error.code : null,
    message: typeof error.message === "string" ? error.message : null,
  };
}

async function request(path: string, init?: RequestInit): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, {
      ...init,
      headers: { Accept: "application/json", ...(init?.headers ?? {}) },
      credentials: "same-origin",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError("无法连接控制面 API，请确认后端服务已启动。", {
      code: "network_error",
      cause: error,
    });
  }
  const payload = await readPayload(response);
  if (!response.ok) {
    const info = errorInfo(payload);
    notifyUnauthorized(response.status);
    throw new ApiError(info.message ?? `控制面请求失败（HTTP ${response.status}）`, {
      status: response.status,
      code: info.code,
    });
  }
  return payload;
}

export async function fetchMe(signal?: AbortSignal): Promise<AuthSession> {
  return parseAuthSession(await request("/api/v1/auth/me", { signal }));
}

export async function login(
  email: string,
  password: string,
  remember: boolean,
): Promise<AuthSession> {
  return parseAuthSession(
    await request("/api/v1/auth/login", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ email, password, remember }),
    }),
  );
}

export async function logout(): Promise<void> {
  await request("/api/v1/auth/logout", { method: "POST" });
}
