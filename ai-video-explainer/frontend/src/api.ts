import type {
  AnalysisRun,
  AnalyzeResponse,
  ApiErrorBody,
  CreateProjectPayload,
  DurationPlanDocument,
  GenerateNarrationResponse,
  GenerateScriptResponse,
  Job,
  Language,
  StartRenderResponse,
  NarrationManifestDocument,
  NarrationRun,
  NarrationTimelineDocument,
  Project,
  ProjectStatus,
  RenderManifestDocument,
  RenderPlanDocument,
  RenderRun,
  ScriptDocument,
  ScriptQualityDocument,
  ScriptRun,
  SelectedScenesDocument,
  StoryDocument,
  SystemStatus,
  TimelineDocument,
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
  startAnalysis: (id: string) =>
    request<AnalyzeResponse>(`/api/projects/${id}/analyze`, { method: "POST" }),
  getAnalysis: (id: string) => request<AnalysisRun>(`/api/projects/${id}/analysis`),
  getTimeline: (id: string) =>
    request<TimelineDocument>(`/api/projects/${id}/timeline`),
  getRenderPlan: (id: string) =>
    request<RenderPlanDocument>(`/api/projects/${id}/render-plan`),
  generateScript: (
    id: string,
    payload: { language: Language; target_duration_seconds: number },
  ) =>
    request<GenerateScriptResponse>(`/api/projects/${id}/generate-script`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getStoryStatus: (id: string) =>
    request<ScriptRun>(`/api/projects/${id}/story-status`),
  getStory: (id: string) =>
    request<StoryDocument>(`/api/projects/${id}/story`),
  getSelectedScenes: (id: string) =>
    request<SelectedScenesDocument>(`/api/projects/${id}/selected-scenes`),
  getDurationPlan: (id: string) =>
    request<DurationPlanDocument>(`/api/projects/${id}/duration-plan`),
  getScript: (id: string) =>
    request<ScriptDocument>(`/api/projects/${id}/script`),
  getScriptQuality: (id: string) =>
    request<ScriptQualityDocument>(`/api/projects/${id}/script-quality`),
  generateNarration: (id: string, payload: { language: Language; voice_id?: string }) =>
    request<GenerateNarrationResponse>(`/api/projects/${id}/generate-narration`, {
      method: "POST",
      body: JSON.stringify(payload),
    }),
  getNarrationStatus: (id: string) =>
    request<NarrationRun>(`/api/projects/${id}/narration-status`),
  getNarrationManifest: (id: string) =>
    request<NarrationManifestDocument>(`/api/projects/${id}/narration`),
  getNarrationTimeline: (id: string) =>
    request<NarrationTimelineDocument>(`/api/projects/${id}/narration/segments`),
  startRender: (id: string) =>
    request<StartRenderResponse>(`/api/projects/${id}/render`, {
      method: "POST",
      body: JSON.stringify({}),
    }),
  getRenderStatus: (id: string) =>
    request<RenderRun>(`/api/projects/${id}/render-status`),
  getRenderManifest: (id: string) =>
    request<RenderManifestDocument>(`/api/projects/${id}/render`),
  narrationSubtitlesText: (id: string, format: "srt" | "vtt" = "srt") =>
    fetch(`${BASE}/api/projects/${id}/narration/subtitles?format=${format}`).then(
      async (response) => {
        if (!response.ok) {
          let detail = `Subtitles unavailable (HTTP ${response.status}).`;
          try {
            const body = (await response.json()) as ApiErrorBody;
            if (body.detail) detail = body.detail;
          } catch {
            // Non-JSON error body; keep the generic message.
          }
          throw new ApiError(response.status, detail);
        }
        return response.text();
      },
    ),
};

/** Browser URL for a project's poster thumbnail (or null pre-PREPARED). */
export function thumbnailUrl(project: Pick<Project, "id" | "thumbnail_path">): string | null {
  return project.thumbnail_path ? `${BASE}/api/projects/${project.id}/thumbnail` : null;
}

/** Browser URL of the narration audio (Phase 6). */
export function narrationAudioUrl(projectId: string): string {
  return `${BASE}/api/projects/${projectId}/narration/audio`;
}

/** Browser URL of the subtitles (SRT by default, VTT via format). */
/** Browser URL of the final rendered video (Phase 7). */
export function renderVideoUrl(projectId: string): string {
  return `${BASE}/api/projects/${projectId}/render/video`;
}

/** Browser URL of the final subtitles sidecar (SRT/VTT). */
export function renderSubtitlesUrl(projectId: string, format: "srt" | "vtt" = "srt"): string {
  return `${BASE}/api/projects/${projectId}/render/subtitles?format=${format}`;
}

export function narrationSubtitlesUrl(projectId: string, format: "srt" | "vtt" = "srt"): string {
  return `${BASE}/api/projects/${projectId}/narration/subtitles?format=${format}`;
}

/** Browser URL for one scene's representative frame (Phase 4). */
export function analysisFrameUrl(projectId: string, sceneId: number): string {
  return `${BASE}/api/projects/${projectId}/analysis/frames/${sceneId}`;
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
    "analyzing",
    "analyzed",
    "scripting",
    "script_ready",
    "narrating",
    "narration_ready",
    "queued",
    "processing",
    "completed",
    "failed",
  ];
  return (known as string[]).includes(value) ? (value as ProjectStatus) : "created";
}
