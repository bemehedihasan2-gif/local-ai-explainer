import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import {
  analysisFrameUrl,
  api,
  ApiError,
  narrationAudioUrl,
  narrationSubtitlesUrl,
  renderSubtitlesUrl,
  renderVideoUrl,
  thumbnailUrl,
  uploadVideo,
} from "./api";
import {
  DURATIONS,
  LANGUAGES,
  SUPPORTED_EXTENSIONS,
  type AnalysisRun,
  type DurationMinutes,
  type DurationPlanDocument,
  type Language,
  type NarrationManifestDocument,
  type NarrationRun,
  type NarrationTimelineDocument,
  type Project,
  type RenderManifestDocument,
  type RenderRun,
  type ScriptDocument,
  type ScriptQualityDocument,
  type ScriptRun,
  type SelectedScenesDocument,
  type StoryDocument,
  type SystemStatus,
  type TimelineDocument,
} from "./types";

/* ------------------------------------------------------------------ */
/*  Small shared bits                                                  */
/* ------------------------------------------------------------------ */

const LANGUAGE_LABEL: Record<Language, string> = {
  en: "English",
  hi: "Hindi",
  bn: "Bengali",
};

const STATUS_LABEL: Record<string, string> = {
  created: "Created",
  uploading: "Uploading",
  validating: "Validating",
  ready: "Ready",
  preprocessing: "Preprocessing",
  prepared: "Prepared",
  analyzing: "Analyzing",
  analyzed: "Analyzed",
  scripting: "Generating script",
  script_ready: "Script ready",
  narrating: "Generating narration",
  narration_ready: "Narration ready",
  rendering: "Rendering final video",
  render_failed: "Render failed",
  completed: "Completed",
  queued: "Queued",
  processing: "Processing",
  failed: "Failed",
};



interface Notice {
  kind: "info" | "warn" | "error";
  title: string;
  body: string;
}

type Connection = "loading" | "ok" | "degraded" | "offline";
type UploadStage = "idle" | "uploading" | "failed" | "ready";

function shortId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

function formatWhen(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
}

function fileSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function clock(seconds: number | null): string {
  if (seconds == null || !Number.isFinite(seconds)) return "—";
  const whole = Math.round(seconds);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

function clockMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms) || ms < 0) return "—";
  const whole = Math.round(ms / 1000);
  const m = Math.floor(whole / 60);
  const s = whole % 60;
  return `${m}:${String(s).padStart(2, "0")}`;
}

interface SrtCue {
  index: number;
  start: string;
  end: string;
  text: string;
}

/** Split an SRT document into its cues for lightweight preview. */
function parseSrt(srt: string): SrtCue[] {
  const cues: SrtCue[] = [];
  for (const rawBlock of srt.split(/\r?\n\r?\n/)) {
    const lines = rawBlock.split(/\r?\n/).map((line) => line.trim());
    const index = Number.parseInt(lines[0] ?? "", 10);
    const times = lines[1] ?? "";
    const arrow = times.indexOf("-->");
    const text = lines.slice(2).join(" ").trim();
    if (!Number.isFinite(index) || arrow < 0 || !text) continue;
    cues.push({
      index,
      start: times.slice(0, arrow).trim(),
      end: times.slice(arrow + 3).trim(),
      text,
    });
  }
  return cues;
}

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/* ------------------------------------------------------------------ */
/*  Pipeline roadmap (mirrors docs/architecture.md)                    */
/* ------------------------------------------------------------------ */

const PIPELINE: { name: string; phase: string; done?: boolean }[] = [
  { name: "Video Upload", phase: "Phase 2", done: true },
  { name: "Preprocessing", phase: "Phase 3", done: true },
  { name: "Scene Detection", phase: "Phase 4", done: true },
  { name: "Speech-to-Text", phase: "Phase 4", done: true },
  { name: "OCR", phase: "Phase 4", done: true },
  { name: "Vision Understanding", phase: "Phase 4", done: true },
  { name: "Timeline Alignment", phase: "Phase 4", done: true },
  { name: "Story Understanding", phase: "Phase 5", done: true },
  { name: "Duration Selection", phase: "Phase 5", done: true },
  { name: "Script Generation", phase: "Phase 5", done: true },
  { name: "TTS Narration", phase: "Phase 6", done: true },
  { name: "Subtitle Generation", phase: "Phase 6", done: true },
  { name: "Audio Mixing", phase: "Phase 7" },
  { name: "FFmpeg Rendering", phase: "Phase 7" },
  { name: "Quality Control", phase: "Phase 7" },
];

/* ------------------------------------------------------------------ */
/*  App                                                                */
/* ------------------------------------------------------------------ */

export default function App() {
  const [connection, setConnection] = useState<Connection>("loading");
  const [system, setSystem] = useState<SystemStatus | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectsLoaded, setProjectsLoaded] = useState(false);

  const [pickedFile, setPickedFile] = useState<File | null>(null);
  const [pickError, setPickError] = useState<string | null>(null);
  const [dragging, setDragging] = useState(false);
  const [language, setLanguage] = useState<Language>("en");
  const [duration, setDuration] = useState<DurationMinutes>(3);

  const [uploadStage, setUploadStage] = useState<UploadStage>("idle");
  const [uploadPercent, setUploadPercent] = useState<number | null>(null);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [view, setView] = useState<Project | null>(null);
  const [analysis, setAnalysis] = useState<AnalysisRun | null>(null);
  const [timeline, setTimeline] = useState<TimelineDocument | null>(null);
  const [scriptRun, setScriptRun] = useState<ScriptRun | null>(null);
  const [story, setStory] = useState<StoryDocument | null>(null);
  const [selectedScenes, setSelectedScenes] =
    useState<SelectedScenesDocument | null>(null);
  const [durationPlan, setDurationPlan] = useState<DurationPlanDocument | null>(null);
  const [script, setScript] = useState<ScriptDocument | null>(null);
  const [scriptQuality, setScriptQuality] = useState<ScriptQualityDocument | null>(null);
  const [scriptLanguage, setScriptLanguage] = useState<Language>("en");
  const [scriptDuration, setScriptDuration] = useState<DurationMinutes>(3);
  const [narrationRun, setNarrationRun] = useState<NarrationRun | null>(null);
  const [narrationManifest, setNarrationManifest] =
    useState<NarrationManifestDocument | null>(null);
  const [narrationTimeline, setNarrationTimeline] =
    useState<NarrationTimelineDocument | null>(null);
  const [narrationSrt, setNarrationSrt] = useState<string | null>(null);
  const [renderRun, setRenderRun] = useState<RenderRun | null>(null);
  const [renderManifest, setRenderManifest] =
    useState<RenderManifestDocument | null>(null);

  const [busyDelete, setBusyDelete] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);

  // Load system status + project history on mount.
  useEffect(() => {
    let cancelled = false;
    async function boot() {
      try {
        const [sys, list] = await Promise.all([
          api.systemStatus(),
          api.listProjects(),
        ]);
        if (cancelled) return;
        setSystem(sys);
        setConnection(sys.status === "ok" ? "ok" : "degraded");
        setProjects(list);
        setNotice(
          sys.ffmpeg.available
            ? null
            : {
                kind: "warn",
                title: "FFmpeg not detected",
                body: `${sys.ffmpeg.setup_hint ?? "Install FFmpeg to validate videos."} Videos cannot be validated until FFmpeg (ffprobe) is installed.`,
              },
        );
      } catch {
        if (cancelled) return;
        setConnection("offline");
        setNotice({
          kind: "error",
          title: "Backend is offline",
          body: "Start the backend from the backend/ folder (`uvicorn app.main:app`) then reload this page.",
        });
      } finally {
        if (!cancelled) setProjectsLoaded(true);
      }
    }
    void boot();
    return () => {
      cancelled = true;
    };
  }, []);

  const refreshProjects = async () => {
    try {
      setProjects(await api.listProjects());
    } catch {
      // Keep whatever is on screen; the top notice explains outages.
    }
  };

  /* ---- file picking (click + drag & drop) ------------------------ */

  const acceptFile = (file: File | undefined) => {
    setUploadError(null);
    setPickError(null);
    setView(null);
    setUploadStage("idle");
    setUploadPercent(null);
    if (!file) return;

    const ext = extensionOf(file.name);
    if (!SUPPORTED_EXTENSIONS.includes(ext)) {
      setPickError(
        `"${file.name}" is not a supported video. Use ${SUPPORTED_EXTENSIONS.join(", ")}. The backend validates again after upload.`,
      );
      return;
    }
    if (file.size === 0) {
      setPickError(`"${file.name}" is empty (0 bytes) — it cannot be a video.`);
      return;
    }
    setPickedFile(file);
  };

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    acceptFile(event.target.files?.[0]);
    event.target.value = ""; // allow re-selecting the same file later
  };

  const onDrop = (event: DragEvent<HTMLDivElement>) => {
    event.preventDefault();
    setDragging(false);
    acceptFile(event.dataTransfer.files?.[0]);
  };

  const clearFile = () => {
    setPickedFile(null);
    setPickError(null);
    if (fileInput.current) fileInput.current.value = "";
  };

  /* ---- upload & validate ----------------------------------------- */

  const uploading = uploadStage === "uploading";

  const startUpload = async () => {
    if (!pickedFile) return;
    setUploadStage("uploading");
    setUploadPercent(0);
    setUploadError(null);
    setNotice(null);
    try {
      const project = await uploadVideo({
        file: pickedFile,
        language,
        targetDurationSeconds: duration * 60,
        onProgress: (progress) => setUploadPercent(progress.percent),
      });
      setUploadStage("ready");
      setUploadPercent(100);
      setView(project);
      setPickedFile(null);
      setNotice({
        kind: "info",
        title: "Video ready — validated & fingerprinted",
        body: `FFprobe confirmed "${project.original_filename}" (${clock(project.duration)}, ${project.width}×${project.height}). Preprocess it next (Phase 3) to build the analysis copy, poster and 16 kHz audio track.`,
      });
      void refreshProjects();
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setUploadStage("failed");
      setUploadError(message);
      if (err instanceof ApiError && err.code === "duplicate_video") {
        setNotice({
          kind: "warn",
          title: "Duplicate video detected",
          body: message,
        });
      } else {
        setNotice({ kind: "error", title: "Upload failed", body: message });
      }
      void refreshProjects(); // FAILED rows become visible in history
    }
  };

  /* ---- Phase 6 narration generation ------------------------------- */

  const [narrationBusy, setNarrationBusy] = useState(false);

  const startNarration = async (project: Project) => {
    setNarrationBusy(true);
    setNotice(null);
    try {
      const response = await api.generateNarration(project.id, {
        language: scriptLanguage,
      });
      if (response.idempotent) {
        setNotice({
          kind: "info",
          title: "Narration already ready",
          body: response.message ?? "Existing narration is still valid; nothing was re-synthesized.",
        });
      }
      setView(await api.getProject(project.id)); // -> narrating
      setProjects(await api.listProjects());
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setNotice({
        kind: "error",
        title: "Could not start narration",
        body: message,
      });
    } finally {
      setNarrationBusy(false);
    }
  };

  /* ---- Phase 5 story + script generation -------------------------- */

  const [scriptBusy, setScriptBusy] = useState(false);

  const startScript = async (project: Project) => {
    setScriptBusy(true);
    setNotice(null);
    try {
      const response = await api.generateScript(project.id, {
        language: scriptLanguage,
        target_duration_seconds: scriptDuration * 60,
      });
      if (response.idempotent) {
        setNotice({
          kind: "info",
          title: "Explanation already ready",
          body: response.message ?? "Existing results are still valid; nothing was re-run.",
        });
      }
      setView(await api.getProject(project.id)); // -> scripting (or script_ready)
      setProjects(await api.listProjects());
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setNotice({
        kind: "error",
        title: "Could not start script generation",
        body: message,
      });
    } finally {
      setScriptBusy(false);
    }
  };

  /* ---- Phase 7 final render --------------------------------------- */

  const [renderBusy, setRenderBusy] = useState(false);

  const startRender = async (project: Project) => {
    setRenderBusy(true);
    setNotice(null);
    try {
      const response = await api.startRender(project.id);
      if (response.idempotent) {
        setNotice({
          kind: "info",
          title: "Final video already ready",
          body: response.message ?? "Existing final video is still valid; nothing was re-encoded.",
        });
      }
      setView(await api.getProject(project.id)); // -> rendering (or completed)
      setProjects(await api.listProjects());
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setNotice({
        kind: "error",
        title: "Could not start rendering",
        body: message,
      });
    } finally {
      setRenderBusy(false);
    }
  };

  /* ---- Phase 3 preprocessing -------------------------------------- */

  const [prepBusy, setPrepBusy] = useState(false);

  const startPreprocess = async (project: Project) => {
    setPrepBusy(true);
    setNotice(null);
    try {
      await api.startPreprocess(project.id);
      setView(await api.getProject(project.id)); // -> preprocessing
      setProjects(await api.listProjects());
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setNotice({
        kind: "error",
        title: "Could not start preprocessing",
        body: message,
      });
    } finally {
      setPrepBusy(false);
    }
  };

  /* ---- Phase 4 analysis ------------------------------------------- */

  const [analyzeBusy, setAnalyzeBusy] = useState(false);

  const startAnalysis = async (project: Project) => {
    setAnalyzeBusy(true);
    setNotice(null);
    try {
      const response = await api.startAnalysis(project.id);
      if (response.idempotent) {
        setNotice({
          kind: "info",
          title: "Analysis already complete",
          body: response.message ?? "Existing results are still valid; nothing was re-run.",
        });
      }
      setView(await api.getProject(project.id)); // -> analyzing
      setProjects(await api.listProjects());
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setNotice({
        kind: "error",
        title: "Could not start analysis",
        body: message,
      });
    } finally {
      setAnalyzeBusy(false);
    }
  };

  // While any project is being preprocessed/analyzed/scripted/narrated, poll
  // project + history so the progress bar and status tags stay honest.
  const anyProcessing = projects.some(
    (p) =>
      p.status === "preprocessing" ||
      p.status === "analyzing" ||
      p.status === "scripting" ||
      p.status === "narrating" ||
      p.status === "rendering",
  );
  useEffect(() => {
    const activeView =
      view &&
      (view.status === "preprocessing" ||
        view.status === "analyzing" ||
        view.status === "scripting" ||
        view.status === "narrating" ||
        view.status === "rendering");
    if (!anyProcessing && !activeView) return;
    const timer = window.setInterval(() => {
      void (async () => {
        try {
          if (activeView) {
            const fresh = await api.getProject(view!.id);
            setView(fresh);
            if (fresh.status === "analyzing") {
              // Live stage label (scene detection, speech, OCR, ...).
              try {
                setAnalysis(await api.getAnalysis(fresh.id));
              } catch {
                // run row may not be visible yet; next tick retries
              }
            }
            if (fresh.status === "scripting") {
              // Live stage label (preparing evidence, understanding story,
              // writing explanation, quality checking, ...).
              try {
                setScriptRun(await api.getStoryStatus(fresh.id));
              } catch {
                // run row may not be visible yet; next tick retries
              }
            }
            if (fresh.status === "narrating") {
              // Live stage label (segmenting, generating voice, measuring,
              // assembling, QC, ...).
              try {
                setNarrationRun(await api.getNarrationStatus(fresh.id));
              } catch {
                // run row may not be visible yet; next tick retries
              }
            }
            if (fresh.status === "rendering") {
              // Live stage label (extracting clips, mixing, burning,
              // encoding, validating, ...).
              try {
                setRenderRun(await api.getRenderStatus(fresh.id));
              } catch {
                // run row may not be visible yet; next tick retries
              }
            }
          }
          setProjects(await api.listProjects());
        } catch {
          // Transient backend hiccup; the next tick retries.
        }
      })();
    }, 1000);
    return () => window.clearInterval(timer);
  }, [anyProcessing, view]);

  // When a project reaches ANALYZED / SCRIPT_READY / NARRATION_READY, load
  // its results once.
  const analyzedId =
    view &&
    (view.status === "analyzed" ||
      view.status === "script_ready" ||
      view.status === "narration_ready")
      ? view.id
      : null;
  useEffect(() => {
    if (!analyzedId) {
      setAnalysis(null);
      setTimeline(null);
      setScriptRun(null);
      setStory(null);
      setSelectedScenes(null);
      setDurationPlan(null);
      setScript(null);
      setScriptQuality(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const run = await api.getAnalysis(analyzedId);
        if (cancelled) return;
        setAnalysis(run);
        const doc = await api.getTimeline(analyzedId);
        if (!cancelled) setTimeline(doc);
      } catch {
        // Assets may be missing (deleted project); leave the panel empty.
      }
      if (
        view?.status !== "script_ready" &&
        view?.status !== "narration_ready"
      )
        return;
      try {
        const [run2, storyDoc, selected, plan, scriptDoc, quality] =
          await Promise.all([
            api.getStoryStatus(analyzedId),
            api.getStory(analyzedId),
            api.getSelectedScenes(analyzedId),
            api.getDurationPlan(analyzedId),
            api.getScript(analyzedId),
            api.getScriptQuality(analyzedId),
          ]);
        if (cancelled) return;
        setScriptRun(run2);
        setStory(storyDoc);
        setSelectedScenes(selected);
        setDurationPlan(plan);
        setScript(scriptDoc);
        setScriptQuality(quality);
        setScriptLanguage(scriptDoc.language);
        setScriptDuration(
          (scriptDoc.target_duration_seconds ?? 180) === 120
            ? 2
            : (scriptDoc.target_duration_seconds ?? 180) === 240
              ? 4
              : 3,
        );
      } catch {
        // Script artifacts may be missing (deleted/replaced); leave empty.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [analyzedId, view?.status]);

  // When a project reaches NARRATION_READY, load its audio/subtitle assets;
  // when it reaches RENDERING/RENDER_FAILED/COMPLETED, load render state.
  const renderStateId =
    view &&
    (view.status === "rendering" ||
      view.status === "render_failed" ||
      view.status === "completed")
      ? view.id
      : null;
  useEffect(() => {
    if (!renderStateId) {
      setRenderRun(null);
      setRenderManifest(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const run = await api.getRenderStatus(renderStateId);
        if (cancelled) return;
        setRenderRun(run);
        if (view?.status === "completed") {
          setRenderManifest(await api.getRenderManifest(renderStateId));
        }
      } catch {
        // Run row may not be visible yet; next tick retries (polling).
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [renderStateId, view?.status]);

  // When a project reaches NARRATION_READY, load its audio/subtitle assets.
  const narrationReadyId =
    view && view.status === "narration_ready" ? view.id : null;
  useEffect(() => {
    if (!narrationReadyId) {
      setNarrationRun(null);
      setNarrationManifest(null);
      setNarrationTimeline(null);
      setNarrationSrt(null);
      return;
    }
    let cancelled = false;
    void (async () => {
      try {
        const [run, manifest, timeline] = await Promise.all([
          api.getNarrationStatus(narrationReadyId),
          api.getNarrationManifest(narrationReadyId),
          api.getNarrationTimeline(narrationReadyId),
        ]);
        if (cancelled) return;
        setNarrationRun(run);
        setNarrationManifest(manifest);
        setNarrationTimeline(timeline);
        try {
          setNarrationSrt(
            await api.narrationSubtitlesText(narrationReadyId, "srt"),
          );
        } catch {
          setNarrationSrt(null); // subtitles text is optional for preview
        }
      } catch {
        // Narration assets may be missing (deleted/replaced); leave empty.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [narrationReadyId]);

  /* ---- history ---------------------------------------------------- */

  const openProject = async (project: Project) => {
    setView(project);
    try {
      setView(await api.getProject(project.id)); // freshest metadata
    } catch {
      // keep the row we already have
    }
  };

  const removeProject = async (project: Project) => {
    const ok = window.confirm(
      `Delete project "${project.original_filename}"?\nThis removes the database record AND its stored video files.`,
    );
    if (!ok) return;
    setBusyDelete(project.id);
    try {
      await api.deleteProject(project.id);
      setProjects((prev) => prev.filter((p) => p.id !== project.id));
      setView((current) => (current?.id === project.id ? null : current));
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setNotice({ kind: "error", title: "Delete failed", body: message });
    } finally {
      setBusyDelete(null);
    }
  };

  const offline = connection === "offline";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <div className="brand-mark" aria-hidden>
            ▶
          </div>
          <div>
            <h1>Local AI Video Explainer</h1>
            <p className="sub">Zero-cost · runs entirely on your PC · CPU + 8 GB RAM friendly</p>
          </div>
        </div>
        <StatusChip connection={connection} />
      </header>

      {connection === "loading" && (
        <div className="card">
          <div className="empty">Contacting backend…</div>
        </div>
      )}

      {connection !== "loading" && (
        <>
          {notice && (
            <div className={`notice ${notice.kind}`} role="status">
              <strong>{notice.title}</strong>
              {notice.body}
            </div>
          )}

          <div className="layout mt-12">
            {/* ---------- Left: upload flow ---------- */}
            <section className="card">
              <h2>New explanation</h2>
              <p className="hint">
                Select a video, pick language and target length, then upload.{" "}
                <span className="phase-tag">Phase 2</span> streams the file, fingerprints
                it (SHA-256) and validates it with FFprobe.
              </p>

              <input
                ref={fileInput}
                type="file"
                accept={SUPPORTED_EXTENSIONS.join(",")}
                style={{ display: "none" }}
                onChange={onFileChange}
              />
              <div
                className={`dropzone dropzone-box ${pickedFile ? "has-file" : ""} ${dragging ? "dragging" : ""}`}
                role="button"
                tabIndex={0}
                onClick={() => !uploading && fileInput.current?.click()}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    fileInput.current?.click();
                  }
                }}
                onDragOver={(event) => {
                  event.preventDefault();
                  setDragging(true);
                }}
                onDragLeave={() => setDragging(false)}
                onDrop={onDrop}
              >
                {pickedFile ? (
                  <>
                    <div className="file-name">{pickedFile.name}</div>
                    <div className="meta">
                      {fileSize(pickedFile.size)} — ready to upload · drag another file to replace
                    </div>
                    <button
                      type="button"
                      className="btn btn-sm mt-8"
                      onClick={(event) => {
                        event.stopPropagation();
                        clearFile();
                      }}
                    >
                      Remove file
                    </button>
                  </>
                ) : (
                  <>
                    <div className="big">⬆</div>
                    <div>Drop a video here or click to browse</div>
                    <div className="meta">
                      {SUPPORTED_EXTENSIONS.join(" · ")} — max {system?.limits.max_upload_size_mb ?? "—"} MB
                    </div>
                  </>
                )}
              </div>
              {pickError && (
                <p className="field-error" role="alert">
                  {pickError}
                </p>
              )}

              <div className="field mt-12">
                <label htmlFor="language-group">Narration language</label>
                <div className="option-row" id="language-group">
                  {LANGUAGES.map((lang) => (
                    <label key={lang.code}>
                      <input
                        type="radio"
                        name="language"
                        value={lang.code}
                        checked={language === lang.code}
                        onChange={() => setLanguage(lang.code)}
                        disabled={uploading}
                      />
                      <span>{lang.label}</span>
                    </label>
                  ))}
                </div>
              </div>

              <div className="field">
                <label htmlFor="duration-group">Explanation duration</label>
                <div className="option-row" id="duration-group">
                  {DURATIONS.map((minutes) => (
                    <label key={minutes}>
                      <input
                        type="radio"
                        name="duration"
                        value={minutes}
                        checked={duration === minutes}
                        onChange={() => setDuration(minutes)}
                        disabled={uploading}
                      />
                      <span>
                        {minutes} min<small>≈ {minutes * 60} s narration target</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              {uploadStage === "uploading" && (
                <div className="upload-progress" role="progressbar" aria-valuenow={uploadPercent ?? 0}>
                  <div className="flex-between">
                    <strong>Uploading & validating…</strong>
                    <span className="muted">{uploadPercent == null ? "—" : `${uploadPercent}%`}</span>
                  </div>
                  <span className="progress-track">
                    <i style={{ width: `${uploadPercent ?? 0}%` }} />
                  </span>
                  <p className="hint">
                    Streaming to disk — the file is never loaded fully into memory. FFprobe
                    validation follows automatically.
                  </p>
                </div>
              )}

              {uploadStage === "failed" && (
                <p className="field-error mt-12" role="alert">
                  {uploadError ?? "Upload failed."}
                </p>
              )}

              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void startUpload()}
                disabled={!pickedFile || uploading || offline || Boolean(pickError)}
              >
                {uploading
                  ? "Uploading…"
                  : uploadStage === "ready"
                    ? "Upload another video"
                    : "Upload & validate video"}
              </button>
              <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
                Phase 3 adds preprocessing: after upload, "Prepare for analysis" queues a
                single background worker to build the low-res analysis copy, poster
                thumbnail and 16 kHz audio track. Then Phase 4 analyzes it and Phase 5
                writes the narration script.
              </p>
            </section>

            {/* ---------- Right: status + details ---------- */}
            <div className="stack">
              {system && <SystemCard system={system} />}
              {system && !system.tts.available && (
                <p className="hint" style={{ margin: "-6px 2px 0", fontSize: 12 }}>
                  ⚠ Local TTS ({system.tts.provider}) not ready — narration stays
                  disabled until a TTS engine and voices are configured
                  (see README, Phase 6).
                </p>
              )}

              {view && (
                <ProjectCard
                  project={view}
                  prepBusy={prepBusy}
                  analyzeBusy={analyzeBusy}
                  analysis={analysis}
                  timeline={timeline}
                  scriptRun={scriptRun}
                  story={story}
                  selectedScenes={selectedScenes}
                  durationPlan={durationPlan}
                  script={script}
                  scriptQuality={scriptQuality}
                  scriptLanguage={scriptLanguage}
                  scriptDuration={scriptDuration}
                  scriptBusy={scriptBusy}
                  llm={system?.llm ?? null}
                  narrationRun={narrationRun}
                  narrationManifest={narrationManifest}
                  narrationTimeline={narrationTimeline}
                  narrationSrt={narrationSrt}
                  narrationBusy={narrationBusy}
                  tts={system?.tts ?? null}
                  renderRun={renderRun}
                  renderManifest={renderManifest}
                  renderBusy={renderBusy}
                  renderConfig={system?.render ?? null}
                  onStartPreprocess={() => void startPreprocess(view)}
                  onStartAnalysis={() => void startAnalysis(view)}
                  onGenerate={() => void startScript(view)}
                  onStartNarration={() => void startNarration(view)}
                  onStartRender={() => void startRender(view)}
                  onScriptLanguage={setScriptLanguage}
                  onScriptDuration={setScriptDuration}
                />
              )}

              <section className="card">
                <div className="flex-between">
                  <h2>Pipeline roadmap</h2>
                  <span className="chip-status">
                    <span className="dot" /> upload · analyze · script · narration live
                  </span>
                </div>
                <ul className="pipeline">
                  {PIPELINE.map((step, i) => (
                    <li key={step.name} className={step.done ? "done" : ""}>
                      <span className="num">{i + 1}</span>
                      <span className="name">{step.name}</span>
                      <span className="when">{step.done ? "✓ done" : step.phase}</span>
                    </li>
                  ))}
                </ul>
              </section>
            </div>
          </div>

          {/* ---------- History ---------- */}
          <section className="card mt-12">
            <div className="flex-between">
              <h2>Projects</h2>
              <button type="button" className="btn btn-sm" onClick={() => void refreshProjects()}>
                Refresh
              </button>
            </div>
            {projects.length === 0 ? (
              <div className="empty">
                {projectsLoaded
                  ? "No projects yet. Upload your first video on the left."
                  : "Loading projects…"}
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>File</th>
                      <th>Lang</th>
                      <th>Length</th>
                      <th>Resolution</th>
                      <th>FPS</th>
                      <th>Audio</th>
                      <th>Status</th>
                      <th>Created</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {projects.map((project) => (
                      <tr key={project.id} className={view?.id === project.id ? "row-open" : ""}>
                        <td className="filename" title={project.original_filename}>
                          {project.original_filename}
                        </td>
                        <td>{LANGUAGE_LABEL[project.language] ?? project.language}</td>
                        <td>{clock(project.duration)}</td>
                        <td className="muted">
                          {project.width && project.height
                            ? `${project.width}×${project.height}`
                            : "—"}
                        </td>
                        <td className="muted">
                          {project.fps != null ? project.fps.toFixed(project.fps % 1 === 0 ? 0 : 2) : "—"}
                        </td>
                        <td className="muted">
                          {project.has_audio == null ? "—" : project.has_audio ? "♪ yes" : "no audio"}
                        </td>
                        <td>
                          <span className={`tag tag-${project.status}`}>
                            {STATUS_LABEL[project.status] ?? project.status}
                          </span>
                        </td>
                        <td className="muted">{formatWhen(project.created_at)}</td>
                        <td>
                          <div className="row-actions">
                            <button
                              type="button"
                              className="btn-sm-link"
                              onClick={() => void openProject(project)}
                            >
                              {view?.id === project.id ? "Open" : "View"}
                            </button>
                            <button
                              type="button"
                              className="btn-danger"
                              disabled={busyDelete === project.id}
                              onClick={() => void removeProject(project)}
                            >
                              {busyDelete === project.id ? "…" : "Delete"}
                            </button>
                          </div>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <footer className="footer-note">
            Local AI Video Explainer — Phase 6: upload, preprocessing, on-device
            analysis (scene detection, speech-to-text, OCR, visual metadata, aligned
            timeline), a story + script written by a small local LLM (llama.cpp) and
            narration voiced locally (Piper) with subtitles timed to the actual audio.
            English/Hindi/Bengali, 2-4 minute targets. Streaming uploads, SHA-256
            fingerprints, SQLite metadata, single background worker. No paid APIs,
            no cloud models, no secrets in source.
          </footer>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Project card: metadata + Phase 3 preprocessing flow                */
/* ------------------------------------------------------------------ */

function ProjectCard({
  project,
  prepBusy,
  analyzeBusy,
  analysis,
  timeline,
  scriptRun,
  story,
  selectedScenes,
  durationPlan,
  script,
  scriptQuality,
  scriptLanguage,
  scriptDuration,
  scriptBusy,
  llm,
  narrationRun,
  narrationManifest,
  narrationTimeline,
  narrationSrt,
  narrationBusy,
  tts,
  renderRun,
  renderManifest,
  renderBusy,
  renderConfig,
  onStartPreprocess,
  onStartAnalysis,
  onGenerate,
  onStartNarration,
  onStartRender,
  onScriptLanguage,
  onScriptDuration,
}: {
  project: Project;
  prepBusy: boolean;
  analyzeBusy: boolean;
  analysis: AnalysisRun | null;
  timeline: TimelineDocument | null;
  scriptRun: ScriptRun | null;
  story: StoryDocument | null;
  selectedScenes: SelectedScenesDocument | null;
  durationPlan: DurationPlanDocument | null;
  script: ScriptDocument | null;
  scriptQuality: ScriptQualityDocument | null;
  scriptLanguage: Language;
  scriptDuration: DurationMinutes;
  scriptBusy: boolean;
  llm: SystemStatus["llm"] | null;
  narrationRun: NarrationRun | null;
  narrationManifest: NarrationManifestDocument | null;
  narrationTimeline: NarrationTimelineDocument | null;
  narrationSrt: string | null;
  narrationBusy: boolean;
  tts: SystemStatus["tts"] | null;
  renderRun: RenderRun | null;
  renderManifest: RenderManifestDocument | null;
  renderBusy: boolean;
  renderConfig: SystemStatus["render"] | null;
  onStartPreprocess: () => void;
  onStartAnalysis: () => void;
  onGenerate: () => void;
  onStartNarration: () => void;
  onStartRender: () => void;
  onScriptLanguage: (language: Language) => void;
  onScriptDuration: (duration: DurationMinutes) => void;
}) {
  const resolution =
    project.width && project.height ? `${project.width} × ${project.height}` : "—";
  const fps =
    project.fps != null
      ? `${project.fps.toFixed(project.fps % 1 === 0 ? 0 : 2)}${project.raw_fps ? ` (${project.raw_fps})` : ""}`
      : "—";

  const rows: { label: string; value: string }[] = [
    { label: "Duration", value: clock(project.duration) },
    { label: "Resolution", value: resolution },
    { label: "FPS", value: fps },
    {
      label: "Audio",
      value:
        project.has_audio == null
          ? "—"
          : project.has_audio
            ? `${project.audio_codec ?? "track present"}`
            : "No audio track",
    },
    { label: "Video codec", value: project.video_codec ?? "—" },
    { label: "Container", value: project.container_format ?? "—" },
    { label: "File size", value: project.file_size != null ? fileSize(project.file_size) : "—" },
    {
      label: "Bitrate",
      value: project.bitrate != null ? `${(project.bitrate / 1000).toFixed(0)} kbps` : "—",
    },
    { label: "Language", value: LANGUAGE_LABEL[project.language] ?? project.language },
    {
      label: "Target length",
      value: `${Math.round(project.target_duration_seconds / 60)} min`,
    },
    { label: "SHA-256", value: project.sha256 ? shortId(project.sha256) + "…" : "—" },
    { label: "Uploaded", value: formatWhen(project.created_at) },
  ];

  if (project.status === "prepared") {
    rows.push(
      {
        label: "Analysis copy",
        value:
          project.analysis_width && project.analysis_height
            ? `${project.analysis_width} × ${project.analysis_height} @ ${project.analysis_fps} fps`
            : "—",
      },
      {
        label: "Audio track (WAV)",
        value: project.audio_path ? "16 kHz mono — speech-to-text input" : "Skipped (no audio)",
      },
      { label: "Prepared", value: project.prepared_at ? formatWhen(project.prepared_at) : "—" },
    );
  }

  const thumb = thumbnailUrl(project);
  const title =
    project.status === "prepared"
      ? "Analysis assets ready"
      : project.status === "preprocessing"
        ? "Preprocessing video"
        : project.status === "analyzing"
          ? "Analyzing video locally"
          : project.status === "analyzed"
            ? "Video analyzed"
            : project.status === "scripting"
              ? "Writing the explanation"
              : project.status === "script_ready"
                ? "Explanation ready"
                : project.status === "narrating"
                  ? "Generating narration locally"
                  : project.status === "narration_ready"
                    ? "Narration ready"
                    : project.status === "rendering"
                      ? "Rendering final video"
                      : project.status === "render_failed"
                        ? "Render failed - retry below"
                        : project.status === "completed"
                          ? "Final video ready"
                          : "Video";

  return (
    <section
      className={`card ${project.status === "prepared" || project.status === "analyzed" ? "ready-card" : ""}`}
    >
      <div className="flex-between">
        <h2>{title}</h2>
        <span className={`tag tag-${project.status}`}>
          {STATUS_LABEL[project.status] ?? project.status}
        </span>
      </div>
      <p className="hint" style={{ marginTop: -6 }}>
        {project.original_filename}
      </p>

      {thumb && (
        <img
          className="thumb"
          src={thumb}
          alt={`Poster frame of ${project.original_filename}`}
        />
      )}

      <div className="kv-grid">
        {rows.map((row) => (
          <div className="kv" key={row.label}>
            <span className="kv-key">{row.label}</span>
            <span className="kv-value" title={row.value}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      {project.status === "preprocessing" && (
        <div
          className="upload-progress"
          role="progressbar"
          aria-valuenow={Math.round(project.progress)}
        >
          <div className="flex-between">
            <strong>Building analysis assets…</strong>
            <span className="muted">{Math.round(project.progress)}%</span>
          </div>
          <span className="progress-track">
            <i style={{ width: `${project.progress}%` }} />
          </span>
          <p className="hint">
            FFmpeg is creating the analysis copy, poster frame and 16 kHz audio track.
            The worker runs one heavy job at a time — this page updates automatically.
          </p>
        </div>
      )}

      {project.status === "ready" && (
        <>
          {project.error_message && (
            <p className="field-error" role="alert">
              Preprocessing failed: {project.error_message} The video itself is fine —
              you can try again.
            </p>
          )}
          <button
            type="button"
            className="btn btn-primary"
            onClick={onStartPreprocess}
            disabled={prepBusy}
          >
            {prepBusy ? "Queuing…" : "Prepare for analysis"}
          </button>
          <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
            Queues the Phase 3 worker: low-res analysis copy (≤640px @ 5 fps), poster
            thumbnail and 16 kHz mono WAV for later speech-to-text.
          </p>
        </>
      )}

      {project.status === "prepared" && (
        <>
          {project.error_message && (
            <p className="field-error" role="alert">
              A previous analysis failed: {project.error_message} The prepared assets are
              still intact — you can try Analyze again.
            </p>
          )}
          <button
            type="button"
            className="btn btn-primary"
            onClick={onStartAnalysis}
            disabled={analyzeBusy}
          >
            {analyzeBusy ? "Queuing…" : "Analyze video"}
          </button>
          <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
            Runs the Phase 4 local pipeline: scene detection, speech-to-text,
            OCR, visual metadata and timeline alignment — all on this PC, no cloud.
          </p>
        </>
      )}

      {project.status === "analyzing" && (
        <div className="upload-progress" role="progressbar" aria-valuenow={Math.round(project.progress)}>
          <div className="flex-between">
            <strong>Analyzing video…</strong>
            <span className="muted">{Math.round(project.progress)}%</span>
          </div>
          <span className="progress-track">
            <i style={{ width: `${project.progress}%` }} />
          </span>
          <p className="hint">
            {analysis?.current_stage ?? "Working"} — scene detection, speech, OCR and
            visual passes run one after another on a single worker thread.
          </p>
        </div>
      )}

      {project.status === "analyzed" && (
        <>
          {project.error_message && (
            <p className="field-error" role="alert">
              A previous script generation failed: {project.error_message} The
              analysis is still intact — you can try again.
            </p>
          )}
          <GeneratePanel
            analysis={analysis}
            llm={llm}
            language={scriptLanguage}
            duration={scriptDuration}
            busy={scriptBusy}
            onLanguage={onScriptLanguage}
            onDuration={onScriptDuration}
            onGenerate={onGenerate}
          />
        </>
      )}

      {project.status === "scripting" && (
        <ScriptingPanel
          project={project}
          scriptRun={scriptRun}
          language={scriptLanguage}
          duration={scriptDuration}
        />
      )}

      {project.status === "script_ready" && (
        <>
          {project.error_message && (
            <p className="field-error" role="alert">
              {project.error_message}
            </p>
          )}
          <ScriptPanel
            project={project}
            scriptRun={scriptRun}
            story={story}
            selectedScenes={selectedScenes}
            durationPlan={durationPlan}
            script={script}
            scriptQuality={scriptQuality}
            language={scriptLanguage}
            duration={scriptDuration}
            busy={scriptBusy}
            llm={llm}
            onLanguage={onScriptLanguage}
            onDuration={onScriptDuration}
            onGenerate={onGenerate}
          />
          <NarrationControls
            tts={tts}
            script={script}
            scriptQuality={scriptQuality}
            busy={narrationBusy}
            language={scriptLanguage}
            onStartNarration={onStartNarration}
          />
        </>
      )}

      {project.status === "narrating" && (
        <>
          {project.error_message && (
            <p className="field-error" role="alert">
              {project.error_message}
            </p>
          )}
          <NarrationProgress
            project={project}
            narrationRun={narrationRun}
            language={scriptLanguage}
          />
        </>
      )}

      {project.status === "narration_ready" && (
        <>
          {project.error_message && (
            <p className="field-error" role="alert">
              {project.error_message}
            </p>
          )}
          <ScriptPanel
            project={project}
            scriptRun={scriptRun}
            story={story}
            selectedScenes={selectedScenes}
            durationPlan={durationPlan}
            script={script}
            scriptQuality={scriptQuality}
            language={scriptLanguage}
            duration={scriptDuration}
            busy={scriptBusy}
            llm={llm}
            onLanguage={onScriptLanguage}
            onDuration={onScriptDuration}
            onGenerate={onGenerate}
          />
          <NarrationPreview
            project={project}
            narrationRun={narrationRun}
            manifest={narrationManifest}
            timeline={narrationTimeline}
            srt={narrationSrt}
            tts={tts}
            busy={narrationBusy}
            language={scriptLanguage}
            onStartNarration={onStartNarration}
          />
          <FinalVideoSetup
            project={project}
            renderConfig={renderConfig}
            busy={renderBusy}
            language={scriptLanguage}
            narrationDurationMs={
              narrationManifest?.generation.duration_ms ??
              narrationRun?.duration_ms ??
              null
            }
            onStartRender={onStartRender}
          />
        </>
      )}

      {project.status === "rendering" && (
        <RenderProgress project={project} renderRun={renderRun} />
      )}

      {project.status === "render_failed" && (
        <>
          {project.error_message && (
            <p className="field-error" role="alert">
              {project.error_message} The narration and script are intact -
              you can retry the render below.
            </p>
          )}
          <FinalVideoSetup
            project={project}
            renderConfig={renderConfig}
            busy={renderBusy}
            onStartRender={onStartRender}
          />
        </>
      )}

      {project.status === "completed" && (
        <>
          <FinalVideoPreview
            project={project}
            renderRun={renderRun}
            manifest={renderManifest}
            busy={renderBusy}
            onStartRender={onStartRender}
          />
        </>
      )}

      {(project.status === "analyzed" ||
        project.status === "script_ready" ||
        project.status === "narration_ready" ||
        project.status === "rendering" ||
        project.status === "render_failed" ||
        project.status === "completed") &&
        analysis && (
          <AnalysisPanel
            project={project}
            analysis={analysis}
            timeline={timeline}
            selectedScenes={selectedScenes}
            durationPlan={durationPlan}
          />
        )}
    </section>
  );
}

/* ------------------------------------------------------------------ */
/*  Phase 5: generation controls + live progress + script preview     */
/* ------------------------------------------------------------------ */

const RENDER_STAGES: { key: string; label: string }[] = [
  { key: "preparing_plan", label: "Preparing render plan" },
  { key: "extracting_clips", label: "Extracting & normalizing scene clips" },
  { key: "assembling_video", label: "Assembling the video timeline" },
  { key: "preparing_original_audio", label: "Preparing original audio" },
  { key: "mixing_audio", label: "Mixing narration + original audio" },
  {
    key: "rendering_final_video",
    label: "Rendering final video (burning subtitles)",
  },
  { key: "finalizing", label: "Finalizing" },
  { key: "qc", label: "Validating final MP4" },
];

function FinalVideoSetup({
  project: _project,
  renderConfig,
  busy,
  language,
  narrationDurationMs,
  selectedSceneCount,
  onStartRender,
}: {
  project: Project;
  renderConfig: SystemStatus["render"] | null;
  busy: boolean;
  language?: Language | null;
  narrationDurationMs?: number | null;
  selectedSceneCount?: number | null;
  onStartRender: () => void;
}) {
  const burn = renderConfig?.subtitle_burn ?? true;
  const fontOk = renderConfig?.subtitle_font_configured ?? false;
  const needsFont =
    burn &&
    (language === "hi" || language === "bn") &&
    renderConfig != null &&
    !fontOk;
  const renderEnabled = Boolean(renderConfig?.enabled);

  const rows = [
    { label: "Language", value: language ? LANGUAGE_LABEL[language] : "—" },
    {
      label: "Narration length",
      value: clockMs(narrationDurationMs ?? null),
    },
    {
      label: "Selected scenes",
      value:
        selectedSceneCount != null
          ? `${selectedSceneCount} clips`
          : "from story plan",
    },
    {
      label: "Output resolution",
      value: renderConfig
        ? `${renderConfig.output_max[0]}×${renderConfig.output_max[1]} max`
        : "—",
    },
    {
      label: "Frame rate",
      value: renderConfig ? `${renderConfig.output_fps} fps` : "—",
    },
    {
      label: "Encoder",
      value: renderConfig
        ? `${renderConfig.codec} · ${renderConfig.preset} · CRF ${renderConfig.crf}`
        : "—",
    },
    {
      label: "Subtitles",
      value: burn
        ? fontOk
          ? "burned into the video (libass)"
          : "burn-in needs a Unicode font"
        : "burn-in disabled",
    },
    {
      label: "Original audio",
      value: !renderConfig?.original_audio
        ? "narration only"
        : renderConfig?.ducking
          ? "mixed, ducked under narration"
          : "mixed at the configured level",
    },
  ];

  return (
    <div className="script-panel mt-12">
      <h3 className="panel-title">Create final video</h3>
      <p className="hint" style={{ margin: 0 }}>
        Phase 7 renders the selected important scenes from the story plan, mixes
        the narration with the original audio, burns the synchronized subtitles
        and encodes a CPU-friendly H.264 MP4 — everything stays on this PC.
      </p>

      <div className="kv-grid" style={{ marginTop: 10 }}>
        {rows.map((row) => (
          <div className="kv" key={row.label}>
            <span className="kv-key">{row.label}</span>
            <span className="kv-value" title={row.value}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      {renderConfig != null && !renderEnabled && (
        <div className="warn-box">
          <strong>Rendering is not enabled</strong>
          <p style={{ margin: "6px 0 0" }}>
            {renderConfig.note ??
              "FFmpeg rendering is disabled in this build's configuration."}
          </p>
        </div>
      )}
      {renderConfig != null && burn && !fontOk && (
        <div className="warn-box">
          <strong>Subtitle burn-in needs a Unicode font</strong>
          <p style={{ margin: "6px 0 0" }}>
            {language === "hi" || language === "bn"
              ? "To burn Hindi/Bengali subtitles correctly, set SUBTITLE_FONT_PATH to a font that covers Devanagari/Bengali (see README, Phase 7 — fonts). The render will fail rather than show boxes."
              : "No subtitle font is configured. Set SUBTITLE_FONT_PATH or SUBTITLE_FONT_NAME in the environment for burned-in subtitles."}
          </p>
        </div>
      )}
      {needsFont && (
        <p className="field-error" role="alert" style={{ marginTop: 8 }}>
          {LANGUAGE_LABEL[language ?? "en"]} subtitles require a configured
          Unicode font — set SUBTITLE_FONT_PATH before rendering.
        </p>
      )}

      <button
        type="button"
        className="btn btn-primary mt-12"
        onClick={onStartRender}
        disabled={busy || !renderEnabled || needsFont}
      >
        {busy ? "Queuing…" : "Create final video"}
      </button>
      <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
        Only the selected scenes are encoded — the original video is not
        re-transcoded end to end. The renderer normalizes clips, ducks the
        original audio under the narration, and validates the finished MP4 with
        FFprobe before marking the project complete.
      </p>
    </div>
  );
}

function RenderProgress({
  project,
  renderRun,
}: {
  project: Project;
  renderRun: RenderRun | null;
}) {
  const current = renderRun?.current_stage ?? null;
  const currentIndex = current
    ? RENDER_STAGES.findIndex((stage) => stage.key === current)
    : -1;
  const stageLabel =
    currentIndex >= 0
      ? RENDER_STAGES[currentIndex].label
      : renderRun?.status === "queued"
        ? "Waiting for the worker…"
        : "Preparing render…";

  return (
    <div className="script-panel mt-12">
      <h3 className="panel-title">Rendering final video</h3>
      <div
        className="upload-progress"
        role="progressbar"
        aria-valuenow={Math.round(project.progress)}
      >
        <div className="flex-between">
          <strong>{stageLabel}</strong>
          <span className="muted">{Math.round(project.progress)}%</span>
        </div>
        <span className="progress-track">
          <i style={{ width: `${project.progress}%` }} />
        </span>
      </div>

      <ul className="stage-list" style={{ marginTop: 12 }}>
        {RENDER_STAGES.map((stage, index) => {
          const done = currentIndex >= 0 && index < currentIndex;
          const active = index === currentIndex;
          return (
            <li
              key={stage.key}
              className={`stage-row${done ? " done" : ""}${active ? " active" : ""}`}
            >
              <span className="stage-dot" aria-hidden />
              <span className="stage-label">{stage.label}</span>
            </li>
          );
        })}
      </ul>

      <p className="hint">
        The single worker is extracting only the selected scene ranges, then it
        mixes the narration with the original audio (ducked so speech stays
        clear), burns subtitles and encodes the MP4. Progress is reported by
        FFmpeg itself — nothing is faked.
      </p>
    </div>
  );
}

function FinalVideoPreview({
  project,
  renderRun,
  manifest,
  busy,
  onStartRender,
}: {
  project: Project;
  renderRun: RenderRun | null;
  manifest: RenderManifestDocument | null;
  busy: boolean;
  onStartRender: () => void;
}) {
  const media = manifest?.media ?? null;
  const generation = manifest?.generation ?? null;
  const qc = manifest?.quality ?? null;
  const score = qc?.quality_score ?? renderRun?.qc_score ?? null;

  const summaryRows = [
    {
      label: "Language",
      value: generation ? LANGUAGE_LABEL[generation.language] : "—",
    },
    {
      label: "Requested length",
      value: generation
        ? `${Math.round(generation.requested_duration_seconds / 60)} min`
        : "—",
    },
    {
      label: "Narration",
      value: clockMs(generation?.narration_duration_ms ?? null),
    },
    {
      label: "Final duration",
      value: clockMs(
        media?.duration_ms ?? renderRun?.output_duration_ms ?? null,
      ),
    },
    {
      label: "Resolution",
      value:
        media?.width && media?.height
          ? `${media.width}×${media.height}`
          : renderRun?.output_width && renderRun?.output_height
            ? `${renderRun.output_width}×${renderRun.output_height}`
            : "—",
    },
    {
      label: "Frame rate",
      value: String(media?.fps ?? renderRun?.output_fps ?? "—"),
    },
    {
      label: "File size",
      value:
        media?.size_bytes != null
          ? fileSize(media.size_bytes)
          : renderRun?.output_size_bytes != null
            ? fileSize(renderRun.output_size_bytes)
            : "—",
    },
    {
      label: "Video codec",
      value: media?.video_codec ?? "—",
    },
    {
      label: "Audio codec",
      value: media?.audio_codec ?? "—",
    },
    {
      label: "Quality score",
      value: score != null ? `${Math.round(score)} / 100` : "—",
    },
  ];

  const chipLabels: { key: string; label: string }[] = [
    { key: "container_score", label: "container" },
    { key: "video_score", label: "video" },
    { key: "audio_score", label: "audio" },
    { key: "timeline_score", label: "sync/timeline" },
    { key: "subtitle_score", label: "subtitles" },
    { key: "decode_score", label: "decodes" },
  ];

  return (
    <div className="script-panel mt-12">
      <h3 className="panel-title">Final video ready</h3>
      <video
        className="video-player"
        controls
        preload="metadata"
        src={renderVideoUrl(project.id)}
      >
        Your browser does not support the video element.
      </video>

      <div className="link-row">
        <a
          className="btn btn-sm"
          href={renderVideoUrl(project.id)}
          download="final.mp4"
        >
          Download MP4
        </a>
        <a
          className="btn btn-sm"
          href={renderSubtitlesUrl(project.id, "srt")}
          download="subtitles.srt"
        >
          Download SRT
        </a>
        <a
          className="btn btn-sm"
          href={renderSubtitlesUrl(project.id, "vtt")}
          download="subtitles.vtt"
        >
          Download VTT
        </a>
      </div>

      <div className="kv-grid" style={{ marginTop: 8 }}>
        {summaryRows.map((row) => (
          <div className="kv" key={row.label}>
            <span className="kv-key">{row.label}</span>
            <span className="kv-value" title={row.value}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      <div className="flex-between" style={{ marginTop: 4 }}>
        <span className="kv-key">Subtitles</span>
        <span className="kv-value">
          {manifest?.subtitles?.burned
            ? "burned into video"
            : "burn-in disabled"}
          {manifest?.subtitles?.sidecar_srt ? " · sidecar SRT kept" : ""}
        </span>
      </div>

      {qc && (
        <>
          <h3 className="panel-title">Final QC</h3>
          <div className="quality-checks">
            {chipLabels.map((item) => {
              const value = qc.scores?.[item.key];
              return value == null ? null : (
                <span key={item.key} className="quality-chip ok">
                  {item.label} {Math.round(value)}
                </span>
              );
            })}
          </div>
          {(manifest?.warnings ?? []).length > 0 && (
            <div className="warn-box">
              <strong>Warnings</strong>
              <ul>
                {(manifest?.warnings ?? []).map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className="field mt-12" style={{ marginBottom: 8 }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={onStartRender}
          disabled={busy}
        >
          {busy ? "Queuing…" : "Render again"}
        </button>
      </div>
      <p className="hint" style={{ margin: 0 }}>
        The MP4 uses the narration recorded for this script, with the original
        audio ducked underneath. To change language, length, voice or scenes,
        regenerate the explanation or narration above — the final video is then
        re-rendered from the updated artifacts.
      </p>
    </div>
  );
}

function GeneratePanel({
  analysis,
  llm,
  language,
  duration,
  busy,
  onLanguage,
  onDuration,
  onGenerate,
}: {
  analysis: AnalysisRun | null;
  llm: SystemStatus["llm"] | null;
  language: Language;
  duration: DurationMinutes;
  busy: boolean;
  onLanguage: (language: Language) => void;
  onDuration: (duration: DurationMinutes) => void;
  onGenerate: () => void;
}) {
  return (
    <div className="generate-panel mt-12">
      <h3 className="panel-title">Generate explanation</h3>
      <p className="hint" style={{ marginTop: 4 }}>
        Analysis complete:{" "}
        <strong>{analysis?.scene_count ?? "—"} scenes</strong>
        <span className="dot-sep">·</span>
        speech{" "}
        <strong>
          {analysis?.transcript_available
            ? "available"
            : "unavailable / skipped"}
        </strong>
        <span className="dot-sep">·</span>
        OCR{" "}
        <strong>{analysis?.ocr_available ? "available" : "none"}</strong>
      </p>

      <div className="field mt-12">
        <label htmlFor="script-language-group">Narration language</label>
        <div className="option-row" id="script-language-group">
          {LANGUAGES.map((lang) => (
            <label key={lang.code}>
              <input
                type="radio"
                name="script-language"
                value={lang.code}
                checked={language === lang.code}
                onChange={() => onLanguage(lang.code)}
                disabled={busy}
              />
              <span>{lang.label}</span>
            </label>
          ))}
        </div>
      </div>

      <div className="field">
        <label htmlFor="script-duration-group">Explanation duration</label>
        <div className="option-row" id="script-duration-group">
          {DURATIONS.map((minutes) => (
            <label key={minutes}>
              <input
                type="radio"
                name="script-duration"
                value={minutes}
                checked={duration === minutes}
                onChange={() => onDuration(minutes)}
                disabled={busy}
              />
              <span>
                {minutes} min<small>≈ {minutes * 60} s narration target</small>
              </span>
            </label>
          ))}
        </div>
      </div>

      {llm && !llm.available && (
        <div className="warn-box">
          <strong>Local language model not ready</strong>
          <p style={{ margin: "6px 0 0" }}>
            {llm.setup_hint ??
              "Set up llama.cpp + a small quantized GGUF model (see README, Phase 5 - first-run model setup). The app never downloads models automatically."}
          </p>
        </div>
      )}

      <button
        type="button"
        className="btn btn-primary mt-12"
        onClick={onGenerate}
        disabled={busy || (llm != null && !llm.available)}
      >
        {busy ? "Queuing…" : "Generate explanation"}
      </button>
      <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
        Runs the Phase 5 local pipeline: story understanding, important-scene
        selection, duration planning and an original narration script in your
        chosen language — all on this PC, no cloud.
      </p>
    </div>
  );
}

function ScriptingPanel({
  project,
  scriptRun,
  language,
  duration,
}: {
  project: Project;
  scriptRun: ScriptRun | null;
  language: Language;
  duration: DurationMinutes;
}) {
  return (
    <div className="upload-progress" role="progressbar" aria-valuenow={Math.round(project.progress)}>
      <div className="flex-between">
        <strong>
          Writing a {duration}-minute {LANGUAGE_LABEL[language] ?? language}{" "}
          explanation…
        </strong>
        <span className="muted">{Math.round(project.progress)}%</span>
      </div>
      <span className="progress-track">
        <i style={{ width: `${project.progress}%` }} />
      </span>
      <p className="hint">
        {scriptRun?.current_stage ?? "Working"} — evidence preparation, story
        understanding, scene scoring, duration planning, writing and quality
        checking run one after another on a single worker thread.
      </p>
    </div>
  );
}

function ScriptPanel({
  project,
  scriptRun,
  story,
  selectedScenes,
  durationPlan,
  script,
  scriptQuality,
  language,
  duration,
  busy,
  llm,
  onLanguage,
  onDuration,
  onGenerate,
}: {
  project: Project;
  scriptRun: ScriptRun | null;
  story: StoryDocument | null;
  selectedScenes: SelectedScenesDocument | null;
  durationPlan: DurationPlanDocument | null;
  script: ScriptDocument | null;
  scriptQuality: ScriptQualityDocument | null;
  language: Language;
  duration: DurationMinutes;
  busy: boolean;
  llm: SystemStatus["llm"] | null;
  onLanguage: (language: Language) => void;
  onDuration: (duration: DurationMinutes) => void;
  onGenerate: () => void;
}) {
  const [copied, setCopied] = useState(false);

  const copyScript = async () => {
    if (!script?.full_text) return;
    try {
      await navigator.clipboard.writeText(script.full_text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1600);
    } catch {
      // Clipboard may be blocked; nothing else to do.
    }
  };

  const summaryRows = [
    { label: "Language", value: script?.language_label ?? LANGUAGE_LABEL[language] },
    {
      label: "Target duration",
      value: `${Math.round((script?.target_duration_seconds ?? duration * 60) / 60)} min`,
    },
    {
      label: "Estimated narration",
      value: scriptQuality
        ? `≈ ${clock(scriptQuality.estimated_duration_seconds)} @ ${scriptQuality.narration_wpm} wpm`
        : "—",
    },
    { label: "Word count", value: script ? String(script.word_count) : "—" },
    {
      label: "Quality score",
      value: scriptQuality != null ? `${scriptQuality.quality_score} / 100` : "—",
    },
    {
      label: "Content type",
      value: story ? `${story.content_type.replace(/_/g, " ")} (${Math.round(story.content_type_confidence * 100)}%)` : "—",
    },
    {
      label: "Important scenes",
      value: selectedScenes ? String(selectedScenes.summary.selected_count) : "—",
    },
    {
      label: "Completed",
      value: scriptRun?.completed_at ? formatWhen(scriptRun.completed_at) : "—",
    },
  ];

  return (
    <div className="script-panel mt-12">
      <h3 className="panel-title">Explanation ready</h3>
      <div className="kv-grid">
        {summaryRows.map((row) => (
          <div className="kv" key={row.label}>
            <span className="kv-key">{row.label}</span>
            <span className="kv-value" title={row.value}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      {story && (
        <>
          <h3 className="panel-title">Story overview</h3>
          <p className="story-premise">{story.premise}</p>
          {story.key_turning_points.length > 0 && (
            <p className="hint" style={{ marginTop: 6 }}>
              Turning points:{" "}
              {story.key_turning_points.map((point) => point.text).join(" · ")}
            </p>
          )}
        </>
      )}

      {selectedScenes && selectedScenes.selected.length > 0 && (
        <>
          <h3 className="panel-title">Important scenes</h3>
          <div className="scene-list">
            {selectedScenes.selected.map((scene) => (
              <div className="scene-row" key={scene.scene_id}>
                {scene.representative_frame ? (
                  <img
                    className="scene-frame"
                    src={analysisFrameUrl(project.id, scene.scene_id)}
                    alt={`Scene ${scene.scene_id} representative frame`}
                    loading="lazy"
                  />
                ) : (
                  <div className="scene-frame scene-frame-empty" aria-hidden>
                    no frame
                  </div>
                )}
                <div className="scene-body">
                  <div className="scene-head">
                    <strong>Scene #{scene.scene_id}</strong>
                    <span className="muted">
                      {clock(scene.start)} → {clock(scene.end)}
                      <span className="dot-sep">·</span>
                      {scene.duration.toFixed(1)} s
                    </span>
                    <span className="tag tag-density" title="Deterministic importance score (0-1)">
                      importance {scene.importance_score.toFixed(2)}
                    </span>
                    {durationPlan && (
                      <span className="tag tag-budget" title="Narration word budget">
                        {durationPlan.scenes.find(
                          (row) => row.scene_id === scene.scene_id,
                        )?.word_budget ?? 0} words
                      </span>
                    )}
                  </div>
                  <div className="scene-meta muted">
                    {scene.reasons.join(" · ")}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      {script && (
        <>
          <div className="flex-between">
            <h3 className="panel-title" style={{ marginBottom: 0 }}>
              Script
            </h3>
            <button type="button" className="btn btn-sm" onClick={() => void copyScript()}>
              {copied ? "Copied ✓" : "Copy script"}
            </button>
          </div>
          <div className="script-text">
            {script.full_text
              .split("\n\n")
              .filter(Boolean)
              .map((paragraph, index) => (
                <p key={index}>{paragraph}</p>
              ))}
          </div>
        </>
      )}

      {scriptQuality && (
        <>
          <h3 className="panel-title">Quality check</h3>
          <div className="quality-checks">
            {scriptQuality.checks.map((check) => (
              <span key={check.check} className={`quality-chip ${check.passed ? "ok" : "warn"}`}>
                {check.passed ? "✓" : "!"} {check.check.replace(/_/g, " ")}
              </span>
            ))}
          </div>
          {scriptQuality.warnings.length > 0 && (
            <div className="warn-box">
              <strong>Warnings</strong>
              <ul>
                {scriptQuality.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      <div className="field mt-12">
        <label htmlFor="regenerate-language-group">Regenerate in</label>
        <div className="option-row" id="regenerate-language-group">
          {LANGUAGES.map((lang) => (
            <label key={lang.code}>
              <input
                type="radio"
                name="regenerate-language"
                value={lang.code}
                checked={language === lang.code}
                onChange={() => onLanguage(lang.code)}
                disabled={busy}
              />
              <span>{lang.label}</span>
            </label>
          ))}
        </div>
      </div>
      <div className="field">
        <label htmlFor="regenerate-duration-group">As</label>
        <div className="option-row" id="regenerate-duration-group">
          {DURATIONS.map((minutes) => (
            <label key={minutes}>
              <input
                type="radio"
                name="regenerate-duration"
                value={minutes}
                checked={duration === minutes}
                onChange={() => onDuration(minutes)}
                disabled={busy}
              />
              <span>
                {minutes} min<small>≈ {minutes * 60} s</small>
              </span>
            </label>
          ))}
        </div>
      </div>
      <button
        type="button"
        className="btn btn-primary mt-12"
        onClick={onGenerate}
        disabled={busy || (llm != null && !llm.available)}
      >
        {busy ? "Queuing…" : "Regenerate explanation"}
      </button>
      <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
        Changing the language or duration re-runs the backend pipeline for
        this video — previous results stay until the new run finishes.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Phase 6: narration controls + live progress + audio/subtitle UI   */
/* ------------------------------------------------------------------ */

type TtsInfo = SystemStatus["tts"] | null;

function voiceForLanguage(tts: TtsInfo, language: Language) {
  return tts?.languages[language] ?? null;
}

function NarrationControls({
  tts,
  script,
  scriptQuality,
  busy,
  language,
  onStartNarration,
}: {
  tts: TtsInfo;
  script: ScriptDocument | null;
  scriptQuality: ScriptQualityDocument | null;
  busy: boolean;
  language: Language;
  onStartNarration: () => void;
}) {
  const engineReady = Boolean(tts?.available);
  const voice = voiceForLanguage(tts, language);
  const voiceReady = engineReady && Boolean(voice?.available);

  const summaryRows = [
    { label: "Script language", value: LANGUAGE_LABEL[language] },
    { label: "Word count", value: script ? String(script.word_count) : "—" },
    {
      label: "Target length",
      value:
        script?.target_duration_seconds != null
          ? `${Math.round(script.target_duration_seconds / 60)} min`
          : "—",
    },
    {
      label: "Estimated narration",
      value: scriptQuality
        ? `≈ ${clock(scriptQuality.estimated_duration_seconds)} @ ${scriptQuality.narration_wpm} wpm`
        : "—",
    },
    {
      label: "TTS engine",
      value: engineReady
        ? `${tts?.provider ?? "piper"} ready`
        : tts == null
          ? "status unknown"
          : `${tts?.provider ?? "piper"} not found`,
    },
    {
      label: "Sample rate",
      value: tts?.settings.sample_rate
        ? `${tts.settings.sample_rate} Hz mono`
        : "—",
    },
  ];

  return (
    <div className="script-panel mt-12">
      <h3 className="panel-title">Generate narration</h3>
      <p className="hint" style={{ margin: 0 }}>
        The approved {LANGUAGE_LABEL[language] ?? language} script is ready to be spoken.
        Phase 6 synthesizes it locally, segment by segment, then times subtitles to the
        real generated audio.
      </p>

      <div className="kv-grid" style={{ marginTop: 10 }}>
        {summaryRows.map((row) => (
          <div className="kv" key={row.label}>
            <span className="kv-key">{row.label}</span>
            <span className="kv-value" title={row.value}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      <h3 className="panel-title">Voices</h3>
      <div className="voice-list" role="list" aria-label="TTS voice availability">
        {LANGUAGES.map((lang) => {
          const entry = voiceForLanguage(tts, lang.code);
          const ok = Boolean(tts?.available && entry?.available);
          return (
            <div className="voice-row" key={lang.code}>
              <span className="voice-lang">{lang.label}</span>
              {ok ? (
                <span className="tag tag-voice-ready">
                  ready · {entry?.voice_id ?? "configured voice"}
                </span>
              ) : (
                <span className="tag tag-voice-missing" title={entry?.note ?? ""}>
                  not configured
                </span>
              )}
            </div>
          );
        })}
      </div>

      {tts != null && !engineReady && (
        <div className="warn-box">
          <strong>Local TTS engine not ready</strong>
          <p style={{ margin: "6px 0 0" }}>
            {tts.setup_hint ??
              "Install a local TTS engine and point TTS_EXECUTABLE_PATH at it (see README, Phase 6 — first-run setup). Voices are never downloaded automatically."}
          </p>
        </div>
      )}
      {engineReady && !voiceReady && (
        <div className="warn-box">
          <strong>No {LANGUAGE_LABEL[language] ?? language} voice configured</strong>
          <p style={{ margin: "6px 0 0" }}>
            {voice?.note ??
              "Add a voice for this language to the TTS voice settings (see README, Phase 6 — voice setup) before generating narration."}
          </p>
        </div>
      )}

      <button
        type="button"
        className="btn btn-primary mt-12"
        onClick={onStartNarration}
        disabled={busy || !engineReady || !voiceReady}
      >
        {busy ? "Queuing…" : "Generate narration"}
      </button>
      <p className="hint" style={{ marginTop: 8, marginBottom: 0 }}>
        Runs the Phase 6 worker: script segmentation, per-segment local TTS, actual
        audio measurement, timeline + SRT/VTT subtitles, assembly and QC — all on this
        PC. Changing the language or duration regenerates the explanation first.
      </p>
    </div>
  );
}

function NarrationProgress({
  project,
  narrationRun,
  language,
}: {
  project: Project;
  narrationRun: NarrationRun | null;
  language: Language;
}) {
  return (
    <div
      className="upload-progress mt-12"
      role="progressbar"
      aria-valuenow={Math.round(project.progress)}
    >
      <div className="flex-between">
        <strong>
          Generating {LANGUAGE_LABEL[language] ?? language} narration…
        </strong>
        <span className="muted">{Math.round(project.progress)}%</span>
      </div>
      <span className="progress-track">
        <i style={{ width: `${project.progress}%` }} />
      </span>
      <p className="hint">
        {narrationRun?.current_stage ?? "Working"} — segmenting the script,
        synthesizing each segment with the local TTS engine, measuring real audio
        durations, then building the timeline, subtitles and QC, one segment at a
        time on the single worker.
      </p>
    </div>
  );
}

function NarrationPreview({
  project,
  narrationRun,
  manifest,
  timeline,
  srt,
  tts,
  busy,
  language,
  onStartNarration,
}: {
  project: Project;
  narrationRun: NarrationRun | null;
  manifest: NarrationManifestDocument | null;
  timeline: NarrationTimelineDocument | null;
  srt: string | null;
  tts: TtsInfo;
  busy: boolean;
  language: Language;
  onStartNarration: () => void;
}) {
  const cues = srt ? parseSrt(srt) : [];
  const segments = timeline?.segments ?? [];
  const engineReady = Boolean(tts?.available);
  const voice = voiceForLanguage(tts, language);
  const voiceReady = engineReady && Boolean(voice?.available);

  const summaryRows = [
    {
      label: "Language",
      value: manifest
        ? LANGUAGE_LABEL[manifest.generation.language]
        : LANGUAGE_LABEL[language],
    },
    {
      label: "Voice",
      value:
        manifest?.generation.voice ??
        manifest?.generation.voice_id ??
        narrationRun?.voice_id ??
        "—",
    },
    {
      label: "TTS provider",
      value:
        manifest?.generation.provider ??
        narrationRun?.provider ??
        tts?.provider ??
        "—",
    },
    {
      label: "Format",
      value:
        manifest
          ? `${manifest.generation.sample_rate} Hz · ${manifest.generation.channels} ch`
          : tts?.settings.sample_rate
            ? `${tts.settings.sample_rate} Hz mono`
            : "—",
    },
    {
      label: "Narration length",
      value: clockMs(
        manifest?.generation.duration_ms ?? narrationRun?.duration_ms,
      ),
    },
    {
      label: "Segments",
      value: String(
        manifest?.generation.segment_count ?? narrationRun?.segment_count ?? "—",
      ),
    },
    {
      label: "Quality score",
      value:
        manifest?.results.quality_score != null
          ? `${manifest.results.quality_score} / 100`
          : "—",
    },
    {
      label: "Completed",
      value: narrationRun?.completed_at
        ? formatWhen(narrationRun.completed_at)
        : "—",
    },
  ];

  const scoreLabels: { key: keyof NarrationManifestDocument["results"]["scores"]; label: string }[] = [
    { key: "audio_score", label: "audio" },
    { key: "timeline_score", label: "timeline" },
    { key: "subtitle_score", label: "subtitles" },
    { key: "mapping_score", label: "scene mapping" },
    { key: "duration_consistency_score", label: "duration" },
  ];

  return (
    <div className="script-panel mt-12">
      <h3 className="panel-title">Narration ready</h3>
      <div className="kv-grid">
        {summaryRows.map((row) => (
          <div className="kv" key={row.label}>
            <span className="kv-key">{row.label}</span>
            <span className="kv-value" title={row.value}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      <h3 className="panel-title">Audio</h3>
      <audio
        className="audio-player"
        controls
        preload="metadata"
        src={narrationAudioUrl(project.id)}
      >
        Your browser does not support the audio element.
      </audio>
      <div className="link-row">
        <a className="btn btn-sm" href={narrationAudioUrl(project.id)} download="narration.wav">
          Download WAV
        </a>
        <a
          className="btn btn-sm"
          href={narrationSubtitlesUrl(project.id, "srt")}
          download="subtitles.srt"
        >
          Download SRT
        </a>
        <a
          className="btn btn-sm"
          href={narrationSubtitlesUrl(project.id, "vtt")}
          download="subtitles.vtt"
        >
          Download VTT
        </a>
      </div>

      <h3 className="panel-title">Subtitles (synced to the real audio)</h3>
      {srt ? (
        cues.length > 0 ? (
          <div className="sub-preview">
            {cues.map((cue) => (
              <div className="sub-cue" key={cue.index}>
                <span className="sub-time">
                  {cue.start} → {cue.end}
                </span>
                <span className="sub-text">{cue.text}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="muted" style={{ marginTop: 6 }}>
            Subtitle file exists but no cues could be parsed.
          </p>
        )
      ) : (
        <p className="muted" style={{ marginTop: 6 }}>
          Subtitle preview unavailable.
        </p>
      )}

      {manifest && (
        <>
          <h3 className="panel-title">Quality check</h3>
          <div className="quality-checks">
            {scoreLabels.map((item) => (
              <span key={item.key} className="quality-chip ok">
                {item.label} {manifest.results.scores[item.key]}
              </span>
            ))}
          </div>
          {manifest.warnings.length > 0 && (
            <div className="warn-box">
              <strong>Warnings</strong>
              <ul>
                {manifest.warnings.map((warning, index) => (
                  <li key={index}>{warning}</li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {segments.length > 0 && (
        <>
          <h3 className="panel-title">Narration segments → scenes</h3>
          <div className="seg-scroll">
            {segments.map((segment) => (
              <div className="seg-row" key={segment.segment_id}>
                <span className="seg-id">
                  #{String(segment.segment_id).padStart(2, "0")}
                </span>
                <span className="seg-time muted">
                  {clockMs(segment.start_ms)} → {clockMs(segment.end_ms)}
                </span>
                <span className="seg-section">
                  {segment.section.replace(/_/g, " ")}
                </span>
                {segment.scene_ids.map((sceneId) => (
                  <span className="tag tag-budget" key={sceneId}>
                    scene {sceneId}
                  </span>
                ))}
                <span className="seg-text" title={segment.text}>
                  {segment.text}
                </span>
              </div>
            ))}
          </div>
        </>
      )}

      {tts != null && !engineReady && (
        <div className="warn-box">
          <strong>Local TTS engine not ready</strong>
          <p style={{ margin: "6px 0 0" }}>
            {tts.setup_hint ??
              "Install a local TTS engine and configure TTS_EXECUTABLE_PATH to regenerate (see README, Phase 6)."}
          </p>
        </div>
      )}
      {engineReady && !voiceReady && (
        <div className="warn-box">
          <strong>No {LANGUAGE_LABEL[language] ?? language} voice configured</strong>
          <p style={{ margin: "6px 0 0" }}>
            {voice?.note ??
              "Configure a voice for this language before regenerating narration."}
          </p>
        </div>
      )}

      <div className="field mt-12" style={{ marginBottom: 8 }}>
        <button
          type="button"
          className="btn btn-primary"
          onClick={onStartNarration}
          disabled={busy || !engineReady || !voiceReady}
        >
          {busy ? "Queuing…" : "Regenerate narration"}
        </button>
      </div>
      <p className="hint" style={{ margin: 0 }}>
        Regeneration keeps the current script and voice. To narrate in another
        language or at another length, change the language/duration above and
        regenerate the explanation — the narration is then synthesized for the
        new script.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Analysis results: run summary + per-scene timeline                */
/* ------------------------------------------------------------------ */

function AnalysisPanel({
  project,
  analysis: run,
  timeline,
  selectedScenes,
  durationPlan,
}: {
  project: Project;
  analysis: AnalysisRun;
  timeline: TimelineDocument | null;
  selectedScenes: SelectedScenesDocument | null;
  durationPlan: DurationPlanDocument | null;
}) {
  const selectedById = new Map<number, { rank: number; importance: number; budget: number }>();
  if (selectedScenes && durationPlan) {
    for (const scene of selectedScenes.selected) {
      selectedById.set(scene.scene_id, {
        rank: selectedScenes.scenes.find((s) => s.scene_id === scene.scene_id)
          ?.selected_rank ?? 0,
        importance: scene.importance_score,
        budget:
          durationPlan.scenes.find((row) => row.scene_id === scene.scene_id)
            ?.word_budget ?? 0,
      });
    }
  }
  const speechLabel =
    run.transcript_available == null
      ? "—"
      : run.transcript_available
        ? `available${run.detected_language ? ` (${run.detected_language})` : ""}`
        : "unavailable / skipped";
  const ocrLabel =
    run.ocr_available == null ? "—" : run.ocr_available ? "available" : "none (not installed)";

  const summaryRows = [
    { label: "Detected language", value: run.detected_language ?? "—" },
    { label: "Scenes", value: run.scene_count != null ? String(run.scene_count) : "—" },
    { label: "Speech", value: speechLabel },
    { label: "OCR", value: ocrLabel },
    { label: "Visual provider", value: run.visual_provider ?? "—" },
    {
      label: "Processing time",
      value: run.processing_seconds != null ? `${run.processing_seconds.toFixed(1)} s` : "—",
    },
    { label: "Completed", value: run.completed_at ? formatWhen(run.completed_at) : "—" },
  ];

  return (
    <div className="analysis-panel mt-12">
      <h3 className="panel-title">Analysis results</h3>
      <div className="kv-grid">
        {summaryRows.map((row) => (
          <div className="kv" key={row.label}>
            <span className="kv-key">{row.label}</span>
            <span className="kv-value" title={row.value}>
              {row.value}
            </span>
          </div>
        ))}
      </div>

      {run.warnings.length > 0 && (
        <div className="warn-box">
          <strong>Warnings</strong>
          <ul>
            {run.warnings.map((warning, i) => (
              <li key={i}>{warning}</li>
            ))}
          </ul>
        </div>
      )}

      {timeline && timeline.scenes.length > 0 && (
        <>
          <h3 className="panel-title">Scene timeline</h3>
          {selectedScenes && (
            <p className="hint" style={{ marginTop: -2 }}>
              Scenes chosen for the narration are highlighted with their
              importance and word budget; the rest are skipped.
            </p>
          )}
          <div className="scene-list">
            {timeline.scenes.map((scene) => {
              const mark = selectedById.get(scene.scene_id);
              return (
                <div
                  className={`scene-row ${mark ? "scene-row-selected" : selectedScenes ? "scene-row-skipped" : ""}`}
                  key={scene.scene_id}
                >
                  {scene.representative_frame ? (
                    <img
                      className="scene-frame"
                      src={analysisFrameUrl(project.id, scene.scene_id)}
                      alt={`Scene ${scene.scene_id} representative frame`}
                      loading="lazy"
                    />
                  ) : (
                    <div className="scene-frame scene-frame-empty" aria-hidden>
                      no frame
                    </div>
                  )}
                  <div className="scene-body">
                    <div className="scene-head">
                      <strong>Scene #{scene.scene_id}</strong>
                      <span className="muted">
                        {clock(scene.start)} → {clock(scene.end)}
                        <span className="dot-sep">·</span>
                        {scene.duration.toFixed(1)} s
                      </span>
                      <span className="tag tag-density" title="Deterministic evidence score (0-100)">
                        density {scene.information_density}
                      </span>
                      {mark && (
                        <>
                          <span className="tag tag-selected" title="Selected for narration">
                            selected #{mark.rank}
                          </span>
                          <span className="tag tag-budget" title="Narration word budget">
                            importance {mark.importance.toFixed(2)} · {mark.budget} words
                          </span>
                        </>
                      )}
                      {!mark && selectedScenes && (
                        <span className="tag tag-skipped" title="Not narrated">
                          skipped
                        </span>
                      )}
                    </div>
                    <div className="scene-meta muted">
                      Speech: {scene.speech_present ? "available" : "unavailable"}
                      <span className="dot-sep">·</span>
                      OCR: {scene.ocr_present ? "available" : "none"}
                      <span className="dot-sep">·</span>
                      Visual:{" "}
                      {scene.visual
                        ? `brightness ${Math.round(scene.visual.brightness)} · blur ${scene.visual.blur_estimate.toFixed(2)}`
                        : "—"}
                    </div>
                  </div>
                </div>
              );
            })}
          </div>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Status chip + system card                                          */
/* ------------------------------------------------------------------ */

function StatusChip({ connection }: { connection: Connection }) {
  const label =
    connection === "loading"
      ? "Checking…"
      : connection === "ok"
        ? "All systems ready"
        : connection === "degraded"
          ? "Degraded (see status)"
          : "Backend offline";
  return (
    <span className={`chip-status ${connection}`}>
      <span className="dot" />
      {label}
    </span>
  );
}

function SystemCard({ system }: { system: SystemStatus }) {
  const rows = [
    { label: "Backend", value: `${system.application.name} v${system.application.version}` },
    { label: "Python", value: `${system.python.implementation} ${system.python.version}` },
    { label: "SQLite", value: system.sqlite.available ? `v${system.sqlite.version} — ready` : "unavailable" },
    { label: "Database", value: system.database.reachable ? "connected & initialized" : "unreachable" },
    {
      label: "FFmpeg / FFprobe",
      value: system.ffmpeg.available
        ? `ready (${system.ffmpeg.ffmpeg.version ?? "?"})`
        : "not installed — uploads will be rejected",
    },
    {
      label: "Speech-to-text",
      value: system.analysis.speech_to_text.model === "ready"
        ? `whisper ${system.analysis.speech_to_text.model_name} ready (${system.analysis.speech_to_text.device})`
        : `whisper ${system.analysis.speech_to_text.model_name} not installed — speech will be skipped`,
    },
    {
      label: "OCR (Tesseract)",
      value: system.analysis.ocr.available
        ? "ready"
        : "not installed — OCR will be skipped",
    },
    {
      label: "Scene detection",
      value: system.analysis.scene_detection.available
        ? `${system.analysis.scene_detection.engine} — ready`
        : "needs FFmpeg",
    },
    {
      label: "Visual analysis",
      value: `${system.analysis.visual.provider} (PIL metadata)`,
    },
    {
      label: "Local LLM (story + script)",
      value: system.llm.available
        ? `${system.llm.provider} ready — ${system.llm.model_name ?? "model"} (${system.llm.threads} threads)`
        : system.llm.model_available
          ? "llama.cpp not found — generation disabled"
          : "no GGUF model — generation disabled",
    },
    { label: "Max upload", value: `${system.limits.max_upload_size_mb} MB` },
    { label: "Supported", value: system.limits.allowed_video_extensions.join(" ") },
  ];

  return (
    <section className="card">
      <div className="flex-between">
        <h2>System status</h2>
        <span className={`chip-status ${system.status === "ok" ? "ok" : "degraded"}`}>
          <span className="dot" />
          {system.status}
        </span>
      </div>
      <div className="table-wrap">
        <table>
          <tbody>
            {rows.map((row) => (
              <tr key={row.label}>
                <th style={{ borderBottom: "none" }}>{row.label}</th>
                <td style={{ borderBottom: "none" }}>{row.value}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {system.notes.length > 0 && (
        <p className="muted" style={{ fontSize: 12.5, marginTop: 8 }}>
          Notes: {system.notes.join(", ")}.
        </p>
      )}
    </section>
  );
}
