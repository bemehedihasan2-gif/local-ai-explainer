import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
} from "react";
import { api, ApiError, uploadVideo } from "./api";
import {
  DURATIONS,
  LANGUAGES,
  SUPPORTED_EXTENSIONS,
  type DurationMinutes,
  type Language,
  type Project,
  type SystemStatus,
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
  queued: "Queued",
  processing: "Processing",
  completed: "Completed",
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

function extensionOf(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot).toLowerCase() : "";
}

/* ------------------------------------------------------------------ */
/*  Pipeline roadmap (mirrors docs/architecture.md)                    */
/* ------------------------------------------------------------------ */

const PIPELINE: { name: string; phase: string; done?: boolean }[] = [
  { name: "Video Upload", phase: "Phase 2", done: true },
  { name: "Preprocessing", phase: "Phase 3" },
  { name: "Scene Detection", phase: "Phase 3" },
  { name: "Speech-to-Text", phase: "Phase 3" },
  { name: "OCR", phase: "Phase 4" },
  { name: "Vision Understanding", phase: "Phase 4" },
  { name: "Story Understanding", phase: "Phase 4" },
  { name: "Duration Selection", phase: "Phase 5" },
  { name: "Script Generation", phase: "Phase 5" },
  { name: "TTS Narration", phase: "Phase 5" },
  { name: "Subtitle Generation", phase: "Phase 5" },
  { name: "Audio Mixing", phase: "Phase 6" },
  { name: "FFmpeg Rendering", phase: "Phase 5" },
  { name: "Quality Control", phase: "Phase 6" },
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
        body: `FFprobe confirmed "${project.original_filename}" (${clock(project.duration)}, ${project.width}×${project.height}). The AI explainer pipeline connects to ready videos in Phase 3+.`,
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

  const generate = (project: Project) => {
    setNotice({
      kind: "info",
      title: "Generation is planned for the next phases",
      body: `"${project.original_filename}" is ready and validated. Script generation, narration (TTS), subtitles and rendering arrive in Phases 3-6 — no fake processing is run here.`,
    });
  };

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
                Phase 2 does not start AI processing — no analysis, no narration, no fake
                results. Generation connects to ready videos in later phases.
              </p>
            </section>

            {/* ---------- Right: status + details ---------- */}
            <div className="stack">
              {system && <SystemCard system={system} />}

              {view && (
                <ReadyVideoCard
                  project={view}
                  canGenerate={view.status === "ready"}
                  onGenerate={() => generate(view)}
                />
              )}

              <section className="card">
                <div className="flex-between">
                  <h2>Pipeline roadmap</h2>
                  <span className="chip-status">
                    <span className="dot" /> upload engine live
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
            Local AI Video Explainer — Phase 2 upload & validation engine. Streaming uploads,
            SHA-256 fingerprints, FFprobe validation, SQLite metadata. No paid APIs, no cloud
            models, no secrets in source.
          </footer>
        </>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------ */
/*  Ready video (metadata) card                                        */
/* ------------------------------------------------------------------ */

function ReadyVideoCard({
  project,
  canGenerate,
  onGenerate,
}: {
  project: Project;
  canGenerate: boolean;
  onGenerate: () => void;
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

  return (
    <section className={`card ${canGenerate ? "ready-card" : ""}`}>
      <div className="flex-between">
        <h2>Video ready</h2>
        <span className={`tag tag-${project.status}`}>
          {STATUS_LABEL[project.status] ?? project.status}
        </span>
      </div>
      <p className="hint" style={{ marginTop: -6 }}>
        {project.original_filename} — validated by FFprobe, ready for the AI pipeline.
      </p>
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
      <button
        type="button"
        className="btn btn-primary"
        onClick={onGenerate}
        disabled={!canGenerate}
        title={
          canGenerate
            ? ""
            : "Generation connects to videos whose validation has finished."
        }
      >
        Generate explanation {canGenerate ? "" : "(pending ready video)"}
      </button>
    </section>
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
