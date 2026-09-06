// Mirrors the FastAPI backend models (backend/app/models/project.py and
// the /api/system/status payload).

export type Language = "en" | "hi" | "bn";
export type DurationMinutes = 2 | 3 | 4;

export const LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "English" },
  { code: "hi", label: "Hindi" },
  { code: "bn", label: "Bengali" },
];

export const DURATIONS: DurationMinutes[] = [2, 3, 4];

export interface Project {
  id: string;
  original_filename: string;
  stored_filename: string | null;
  input_path: string | null;
  duration: number | null;
  width: number | null;
  height: number | null;
  fps: number | null;
  language: Language;
  target_duration_seconds: number;
  status: string;
  progress: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export interface CreateProjectPayload {
  original_filename?: string | null;
  language: Language;
  target_duration_minutes: DurationMinutes;
}

export interface FfmpegInfo {
  available: boolean;
  version: string | null;
  path: string | null;
  setup_hint: string | null;
}

export interface SystemStatus {
  status: string;
  notes: string[];
  application: { name: string; version: string; environment: string };
  python: { version: string; implementation: string };
  ffmpeg: {
    available: boolean;
    ffmpeg: { available: boolean; version: string | null; path: string | null };
    ffprobe: { available: boolean; version: string | null; path: string | null };
    setup_hint: string | null;
  };
  sqlite: { available: boolean; version: string };
  database: { path: string; initialized: boolean; reachable: boolean; error: string | null };
  storage: {
    ok: boolean;
    directories: { name: string; path: string; exists: boolean; writable: boolean }[];
  };
  concurrency: { heavy_jobs: number };
  limits: { max_upload_size_mb: number };
  phase: string;
  message: string;
}

export interface ApiErrorBody {
  detail?: string;
  error?: string;
}
