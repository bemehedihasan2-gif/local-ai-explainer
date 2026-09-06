import type {
  ApiErrorBody,
  CreateProjectPayload,
  Project,
  SystemStatus,
} from "./types";

// Relative URLs keep the app portable: Vite proxies /api to the backend
// during development (see vite.config.ts). Set VITE_API_BASE to point
// somewhere else when the frontend is served separately.
const BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "";

export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${BASE}${path}`, {
      headers: { "Content-Type": "application/json" },
      ...init,
    });
  } catch {
    throw new ApiError(
      0,
      "Cannot reach the backend. Start it from the backend/ folder (uvicorn app.main:app) and reload.",
    );
  }

  if (!response.ok) {
    let detail = `Request failed (HTTP ${response.status}).`;
    let code: string | undefined;
    try {
      const body = (await response.json()) as ApiErrorBody;
      if (body.detail) detail = body.detail;
      code = body.error;
    } catch {
      // Non-JSON error body; keep the generic message.
    }
    throw new ApiError(response.status, detail, code);
  }
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  systemStatus: () => request<SystemStatus>("/api/system/status"),
  listProjects: () => request<Project[]>("/api/projects"),
  createProject: (payload: CreateProjectPayload) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: "DELETE" }),
};
