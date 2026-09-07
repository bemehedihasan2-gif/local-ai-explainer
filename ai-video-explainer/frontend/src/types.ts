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
  | "scripting"
  | "script_ready"
  | "narrating"
  | "narration_ready"
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

export type ContentType =
  | "movie"
  | "short_film"
  | "gameplay"
  | "education"
  | "news"
  | "sports"
  | "tutorial"
  | "lecture"
  | "screen_recording"
  | "nature"
  | "animal"
  | "social_video"
  | "general";

// Phase 5: one story+script run per project (summary only - payloads live
// in JSON files under analysis/story/, never in the API).
export interface ScriptRun {
  id: string;
  project_id: string;
  status: "queued" | "running" | "completed" | "failed";
  current_stage: string | null;
  started_at: string | null;
  completed_at: string | null;
  language: Language;
  target_duration_seconds: number;
  content_type: ContentType | null;
  content_type_confidence: number | null;
  selected_scene_count: number | null;
  word_count: number | null;
  quality_score: number | null;
  estimated_duration_seconds: number | null;
  error_message: string | null;
  warnings: string[];
}

export interface GenerateScriptResponse {
  idempotent: boolean;
  status: string;
  script_run_id?: string;
  job?: Job;
  script_run?: ScriptRun;
  message?: string;
}

// Phase 6: one narration (TTS) run per project - summary only. The audio
// itself is streamed from the dedicated /narration/audio endpoint.
export interface NarrationRun {
  id: string;
  project_id: string;
  status: "queued" | "running" | "completed" | "failed";
  current_stage: string | null;
  started_at: string | null;
  completed_at: string | null;
  language: Language;
  voice_id: string | null;
  provider: string | null;
  audio_path: string | null;
  subtitle_path: string | null;
  duration_ms: number | null;
  segment_count: number | null;
  quality_score: number | null;
  error_message: string | null;
  warnings: string[];
}

export interface GenerateNarrationResponse {
  idempotent: boolean;
  status: string;
  tts_run_id?: string;
  job?: Job;
  tts_run?: NarrationRun;
  message?: string;
}

export interface NarrationTimelineDocument {
  schema_version: number;
  sample_rate: number;
  channels: number;
  gap_ms: { within_section: number; between_sections: number; lead_in: number };
  total_duration_ms: number;
  segments: {
    segment_id: number;
    sequence: number;
    text: string;
    scene_ids: number[];
    section: string;
    section_index: number;
    start_ms: number;
    end_ms: number;
    duration_ms: number;
    audio_path: string;
  }[];
}

export interface NarrationManifestDocument {
  schema_version: number;
  generated_at: string;
  source: { sha256: string | null; original_filename: string | null };
  generation: {
    language: Language;
    voice: string | null;
    voice_id: string | null;
    provider: string;
    sample_rate: number;
    channels: number;
    segment_count: number;
    duration_ms: number;
    normalization: {
      applied_gain_db: number;
      before: { peak_db: number; mean_db: number; clip_ratio: number };
      after: { peak_db: number; mean_db: number; clip_ratio: number };
    };
    gap_ms: { within_section: number; between_sections: number; lead_in: number };
  };
  results: {
    quality_score: number;
    scores: {
      audio_score: number;
      timeline_score: number;
      subtitle_score: number;
      mapping_score: number;
      duration_consistency_score: number;
    };
    processing_seconds: number;
    assets: {
      segments_dir: string;
      segments: string;
      timeline: string;
      audio: string;
      quality: string;
      subtitles_srt: string;
      subtitles_vtt: string;
      manifest: string;
    };
  };
  warnings: string[];
}

export interface StoryDocument {
  schema_version: number;
  content_type: ContentType;
  content_type_confidence: number;
  premise: string;
  main_entities: string[];
  locations: string[];
  chronological_events: { text: string; scene_ids: number[] }[];
  key_turning_points: { text: string; scene_ids: number[] }[];
  beginning: { text: string; scene_ids: number[] }[];
  middle: { text: string; scene_ids: number[] }[];
  ending: { text: string; scene_ids: number[] }[];
  cause_effect: { cause: string; effect: string; scene_ids: number[] }[];
  important_facts: { text: string; scene_ids: number[] }[];
  uncertain_points: string[];
  evidence_scene_ids: number[];
  scene_count: number;
  scene_scores: Record<string, number>;
  warnings: string[];
}

export interface SelectedScene {
  scene_id: number;
  start: number;
  end: number;
  duration: number;
  importance_score: number;
  reasons: string[];
  representative_frame: string | null;
}

export interface SelectedScenesDocument {
  schema_version: number;
  summary: {
    scene_count: number;
    selected_count: number;
    selected_ids: number[];
    max_selected_scenes: number;
  };
  scenes: {
    scene_id: number;
    importance_score: number;
    selected: boolean;
    selected_rank: number | null;
    reasons: string[];
  }[];
  selected: SelectedScene[];
  warnings: string[];
}

export interface DurationPlanDocument {
  schema_version: number;
  target_duration_seconds: number;
  target_word_min: number;
  target_word_max: number;
  target_word_mid: number;
  narration_wpm: number;
  estimated_duration_seconds: number;
  scenes: {
    scene_id: number;
    start: number;
    end: number;
    importance_score: number;
    reasons: string[];
    word_budget: number;
    evidence: "speech" | "ocr" | "visual" | "none";
    selected: boolean;
  }[];
  total_word_budget: number;
  warnings: string[];
}

export interface ScriptSection {
  scene_ids: number[];
  purpose: string;
  word_budget: number;
  text: string;
}

export interface ScriptDocument {
  schema_version: number;
  language: Language;
  language_label: string;
  target_duration_seconds: number | null;
  content_type: ContentType;
  content_type_confidence: number | null;
  sections: ScriptSection[];
  full_text: string;
  word_count: number;
  estimated_duration_seconds: number;
  warnings: string[];
}

export interface ScriptQualityDocument {
  schema_version: number;
  quality_score: number;
  scores: {
    grounding_score: number;
    coverage_score: number;
    coherence_score: number;
    duration_fit_score: number;
    chronology_score: number;
    language_score: number;
    originality_score: number;
  };
  formula: Record<string, number>;
  checks: { check: string; passed: boolean; severity: string; message: string }[];
  warnings: string[];
  word_count: number;
  target_word_min: number;
  target_word_max: number;
  estimated_duration_seconds: number;
  narration_wpm: number;
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
  llm: {
    provider: string;
    available: boolean;
    executable_available: boolean;
    model_available: boolean;
    model_name: string | null;
    threads: number | null;
    context_size: number | null;
    max_tokens: number | null;
    temperature: number | null;
    setup_hint: string | null;
    note: string;
  };
  tts: {
    provider: string;
    available: boolean;
    executable_available: boolean;
    languages: Record<
      Language,
      {
        voice_id: string | null;
        available: boolean;
        configured: boolean;
        model_available: boolean;
        sample_rate: number | null;
        note: string | null;
      }
    >;
    voices: {
      id: string;
      language: Language;
      voice_id: string;
      available: boolean;
      sample_rate: number | null;
      note: string | null;
    }[];
    setup_hint: string | null;
    settings: { sample_rate: number; channels: number; timeout_seconds: number };
    note: string;
  };
  phase: string;
  message: string;
}

export interface ApiErrorBody {
  detail?: string;
  error?: string;
}
