import { ApiError, type OverviewResponse, parseOverview } from "./types";

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

function errorObject(payload: unknown): unknown {
  if (typeof payload !== "object" || payload === null) {
    return null;
  }
  if ("error" in payload) {
    return (payload as { error?: unknown }).error;
  }
  if ("detail" in payload) {
    const detail = (payload as { detail?: unknown }).detail;
    if (typeof detail === "object" && detail !== null && "error" in detail) {
      return (detail as { error?: unknown }).error;
    }
  }
  return null;
}

function errorMessage(payload: unknown, fallback: string): string {
  const error = errorObject(payload);
  if (typeof error === "object" && error !== null && "message" in error) {
    const message = (error as { message?: unknown }).message;
    if (typeof message === "string" && message.trim()) {
      return message;
    }
  }
  return fallback;
}

function errorField(payload: unknown, field: "code" | "request_id"): string | null {
  const error = errorObject(payload);
  if (typeof error !== "object" || error === null || !(field in error)) {
    return null;
  }
  const value = (error as Record<string, unknown>)[field];
  return typeof value === "string" ? value : null;
}

export async function requestJson(path: string, signal?: AbortSignal): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(path, {
      signal,
      headers: { Accept: "application/json" },
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
    throw new ApiError(
      errorMessage(payload, `控制面请求失败（HTTP ${response.status}）`),
      {
        status: response.status,
        code: errorField(payload, "code"),
        requestId: errorField(payload, "request_id"),
      },
    );
  }
  return payload;
}

export async function fetchOverview(signal?: AbortSignal): Promise<OverviewResponse> {
  const payload = await requestJson("/api/v1/overview", signal);
  return parseOverview(payload);
}

export async function fetchReadyz(signal?: AbortSignal): Promise<{ ok: true }> {
  let response: Response;
  try {
    response = await fetch("/readyz", {
      signal,
      headers: { Accept: "application/json" },
      credentials: "same-origin",
    });
  } catch (error) {
    if (error instanceof DOMException && error.name === "AbortError") {
      throw error;
    }
    throw new ApiError("就绪检查不可达", { code: "network_error", cause: error });
  }

  if (!response.ok) {
    throw new ApiError(`就绪检查返回 HTTP ${response.status}`, {
      status: response.status,
      code: "not_ready",
    });
  }
  return { ok: true };
}
