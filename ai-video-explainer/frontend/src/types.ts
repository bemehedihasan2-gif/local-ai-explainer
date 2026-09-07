// Mirrors the FastAPI backend models (backend/app/models/project.py and
// the /api/system/status payload).

export type Language = "en" | "hi" | "bn";
export type DurationMinutes = 2 | 3 | 4;

export const LANGUAGES: { code: Language; label: string }[] = [
  { code: "en", label: "English" },
  { code: "hi", label: "Hindi" },
  { code: "bn", label: "বাংলা" },
];

export const DURATIONS: DurationMinutes[] = [2, 3, 4];

// Keep in sync with backend/app/config.py: allowed_video_extensions.
export const SUPPORTED_EXTENSIONS = [
  ".mp4",
  ".mkv",
  ".avi",
  ".mov",
  ".webm",
  ".m4v",
  ".mpeg",
  ".mpg",
  ".ts",
];

export type ProjectStatus =
  | "created"
  | "uploading"
  | "validating"
  | "ready"
  | "preprocessing"
  | "prepared"
  | "analyzing"
  | "analyzed"
  | "queued"
  | "processing"
  | "completed"
  | "failed";

export interface Project {
  id: string;
  original_filename: string;
  file_size: number | null;
  sha256: string | null;
  duration: number | null; // seconds
  width: number | null;
  height: number | null;
  fps: number | null; // normalized, e.g. 29.97
  raw_fps: string | null; // e.g. "30000/1001"
  video_codec: string | null;
  audio_codec: string | null;
  container_format: string | null;
  bitrate: number | null;
  has_video: boolean | null;
  has_audio: boolean | null;
  // Phase 3 analysis assets (relative paths inside the project folder).
  analysis_path: string | null;
  analysis_width: number | null;
  analysis_height: number | null;
  analysis_fps: number | null;
  thumbnail_path: string | null;
  audio_path: string | null;
  prepared_at: string | null;
  language: Language;
  target_duration_seconds: number;
  status: ProjectStatus;
  progress: number;
  error_message: string | null;
  created_at: string;
  updated_at: string;
}

export type JobStatus = "queued" | "running" | "completed" | "failed";

export interface Job {
  id: string;
  project_id: string;
  stage: string;
  status: JobStatus;
  progress: number;
  error_message: string | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

// Phase 4: one analysis run per project (summary only - transcripts and
// OCR payloads live in JSON files on disk, never in the API).
export interface AnalysisRun {
  id: string;
  project_id: string;
  status: "queued" | "running" | "completed" | "failed";
  current_stage: string | null;
  started_at: string | null;
  completed_at: string | null;
  detected_language: string | null;
  language_probability: number | null;
  scene_count: number | null;
  transcript_available: boolean | null;
  ocr_available: boolean | null;
  visual_provider: string | null;
  processing_seconds: number | null;
  error_message: string | null;
  warnings: string[];
}

export interface AnalyzeResponse {
  idempotent: boolean;
  status: string;
  analysis_id?: string;
  job?: Job;
  analysis?: AnalysisRun;
  message?: string;
}

export interface TimelineScene {
  scene_id: number;
  start: number;
  end: number;
  duration: number;
  representative_timestamp: number;
  representative_frame: string | null;
  speech_present: boolean;
  speech: unknown[];
  ocr_present: boolean;
  ocr: unknown[];
  visual: {
    width: number;
    height: number;
    brightness: number;
    blur_estimate: number;
    complexity: number;
  } | null;
  information_density: number;
}

export interface TimelineDocument {
  schema_version: number;
  duration_seconds: number;
  summary: {
    scene_count: number;
    speech_scenes: number;
    ocr_scenes: number;
    total_words: number;
    detected_language: string | null;
  };
  scenes: TimelineScene[];
}

export interface CreateProjectPayload {
  original_filename?: string | null;
  language: Language;
  target_duration_minutes: DurationMinutes;
}

export type UploadProgress = {
  /** Bytes sent so far (from the browser, network %). */
  loaded: number;
  /** Total bytes when known. */
  total: number | null;
  /** 0-100 percentage of the transfer (null until the total is known). */
  percent: number | null;
};

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
  worker: {
    running: boolean;
    queue_size: number;
    active_job: string | null;
  };
  limits: {
    max_upload_size_mb: number;
    upload_chunk_size_bytes: number;
    ffprobe_timeout_seconds: number;
    allowed_video_extensions: string[];
  };
  preprocess: {
    analysis_width: number;
    analysis_fps: number;
    analysis_encoder_preset: string;
    analysis_crf: number;
    thumbnail_width: number;
    audio_sample_rate: number;
    audio_channels: number;
    preprocess_timeout_seconds: number;
  };
  analysis: {
    scene_detection: { engine: string; available: boolean; note: string };
    speech_to_text: {
      package: string;
      model: string;
      model_name: string;
      device: string;
      compute_type: string;
      language_mode: string;
    };
    ocr: { available: boolean; binary: string | null; setup_hint: string | null };
    visual: { provider: string; note: string };
    settings: {
      whisper_model: string;
      scene_threshold: number;
      min_scene_duration_seconds: number;
      max_scenes: number;
      ocr_enabled: boolean;
      ocr_frame_limit: number;
      visual_analysis_enabled: boolean;
    };
  };
  phase: string;
  message: string;
}

export interface ApiErrorBody {
  detail?: string;
  error?: string;
}
