import type {
  ApiErrorBody,
  CreateProjectPayload,
  Job,
  Language,
  Project,
  ProjectStatus,
  SystemStatus,
  UploadProgress,
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

/**
 * Upload a video with real transfer progress via XMLHttpRequest (fetch does
 * not expose upload progress). The file is streamed by the browser in chunks;
 * the backend enforces size limits, fingerprints and FFprobe validation.
 */
export function uploadVideo(opts: {
  file: File;
  language: Language;
  targetDurationSeconds: number;
  onProgress?: (progress: UploadProgress) => void;
}): Promise<Project> {
  return new Promise<Project>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/api/projects/upload`);
    xhr.responseType = "json";

    xhr.upload.onprogress = (event) => {
      if (!opts.onProgress) return;
      const totalKnown = event.lengthComputable && event.total > 0;
      opts.onProgress({
        loaded: event.loaded,
        total: totalKnown ? event.total : null,
        percent: totalKnown ? Math.min(100, Math.round((event.loaded / event.total) * 100)) : null,
      });
    };

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(xhr.response as Project);
        return;
      }
      let detail = `Upload failed (HTTP ${xhr.status}).`;
      let code: string | undefined;
      try {
        const body = xhr.response as ApiErrorBody;
        if (body && body.detail) {
          // FastAPI validation errors use arrays; surface them readably.
          detail = Array.isArray(body.detail) ? "The request was rejected by the server." : body.detail;
        }
        if (body && typeof body.error === "string") code = body.error;
      } catch {
        // Ignore malformed bodies; keep the generic message.
      }
      reject(new ApiError(xhr.status, detail, code));
    };
    xhr.onerror = () =>
      reject(
        new ApiError(
          0,
          "Upload interrupted — the connection to the backend was lost. No partial file was kept.",
        ),
      );

    const form = new FormData();
    form.append("file", opts.file, opts.file.name);
    form.append("language", opts.language);
    form.append("target_duration", String(opts.targetDurationSeconds));
    xhr.send(form);
  });
}

export const api = {
  health: () => request<{ status: string }>("/api/health"),
  systemStatus: () => request<SystemStatus>("/api/system/status"),
  listProjects: () => request<Project[]>("/api/projects"),
  getProject: (id: string) => request<Project>(`/api/projects/${id}`),
  createProject: (payload: CreateProjectPayload) =>
    request<Project>("/api/projects", {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  deleteProject: (id: string) =>
    request<void>(`/api/projects/${id}`, { method: "DELETE" }),
  startPreprocess: (id: string) =>
    request<Job>(`/api/projects/${id}/preprocess`, { method: "POST" }),
  listJobs: (id: string) => request<Job[]>(`/api/projects/${id}/jobs`),
};

/** Browser URL for a project's poster thumbnail (or null pre-PREPARED). */
export function thumbnailUrl(project: Pick<Project, "id" | "thumbnail_path">): string | null {
  return project.thumbnail_path ? `${BASE}/api/projects/${project.id}/thumbnail` : null;
}

/** Narrow a free-form project status into a known value. */
export function normalizeStatus(value: string): ProjectStatus {
  const known: ProjectStatus[] = [
    "created",
    "uploading",
    "validating",
    "ready",
    "preprocessing",
    "prepared",
    "queued",
    "processing",
    "completed",
    "failed",
  ];
  return (known as string[]).includes(value) ? (value as ProjectStatus) : "created";
}
