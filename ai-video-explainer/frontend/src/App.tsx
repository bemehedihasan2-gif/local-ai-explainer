import { useEffect, useRef, useState, type ChangeEvent } from "react";
import { api, ApiError } from "./api";
import {
  DURATIONS,
  LANGUAGES,
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
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

/* ------------------------------------------------------------------ */
/*  Pipeline roadmap (mirrors docs/architecture.md)                    */
/* ------------------------------------------------------------------ */

const PIPELINE: { name: string; phase: string }[] = [
  { name: "Video Upload", phase: "Phase 2" },
  { name: "Preprocessing", phase: "Phase 2" },
  { name: "Scene Detection", phase: "Phase 4" },
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

  const [fileName, setFileName] = useState<string | null>(null);
  const [fileMeta, setFileMeta] = useState<string | null>(null);
  const [language, setLanguage] = useState<Language>("en");
  const [duration, setDuration] = useState<DurationMinutes>(3);

  const [generating, setGenerating] = useState(false);
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
                body: sys.ffmpeg.setup_hint ?? "Install FFmpeg to enable video processing in later phases.",
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

  const pickFile = (file: File | undefined) => {
    if (!file) return;
    setFileName(file.name);
    setFileMeta(fileSize(file.size));
  };

  const onFileChange = (event: ChangeEvent<HTMLInputElement>) => {
    pickFile(event.target.files?.[0]);
  };

  const generate = async () => {
    setGenerating(true);
    setNotice(null);
    try {
      const created = await api.createProject({
        original_filename: fileName,
        language,
        target_duration_minutes: duration,
      });
      setProjects((prev) => [created, ...prev.filter((p) => p.id !== created.id)]);
      setNotice({
        kind: "info",
        title: "Project registered — processing arrives in Phase 2",
        body: `Project ${shortId(created.id)} was created (${LANGUAGE_LABEL[created.language]}, ${duration} min). In Phase 2 the Generate button will upload the video, analyze it, and run the real AI pipeline end-to-end.`,
      });
    } catch (err) {
      const message = err instanceof ApiError ? err.message : String(err);
      setNotice({
        kind: "error",
        title: "Could not create the project",
        body: message,
      });
    } finally {
      setGenerating(false);
    }
  };

  const removeProject = async (project: Project) => {
    const ok = window.confirm(
      `Delete project "${project.original_filename}"? This removes its record and any job history.`,
    );
    if (!ok) return;
    setBusyDelete(project.id);
    try {
      await api.deleteProject(project.id);
      setProjects((prev) => prev.filter((p) => p.id !== project.id));
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
            {/* ---------- Left: create panel ---------- */}
            <section className="card">
              <h2>New explanation</h2>
              <p className="hint">
                Pick a video, language and length. <span className="phase-tag">Phase 1</span>{" "}
                registers the project; upload + analysis connect in Phase 2.
              </p>

              <input
                ref={fileInput}
                type="file"
                accept="video/*"
                style={{ display: "none" }}
                onChange={onFileChange}
              />
              <button
                type="button"
                className={`dropzone ${fileName ? "has-file" : ""}`}
                onClick={() => fileInput.current?.click()}
                disabled={offline}
              >
                {fileName ? (
                  <>
                    <div className="file-name">{fileName}</div>
                    <div className="meta">{fileMeta} — selected locally, upload in Phase 2</div>
                  </>
                ) : (
                  <>
                    <div className="big">⬆</div>
                    <div>Click to choose a video file</div>
                    <div className="meta">Movies, gameplay, tutorials, lectures, sports, screen recordings…</div>
                  </>
                )}
              </button>

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
                      />
                      <span>
                        {minutes} min<small>≈ {minutes * 150} word script</small>
                      </span>
                    </label>
                  ))}
                </div>
              </div>

              <button
                type="button"
                className="btn btn-primary"
                onClick={() => void generate()}
                disabled={generating || offline}
              >
                {generating ? "Registering…" : "Generate explanation"}
              </button>
              <p className="hint" style={{ marginTop: 10, marginBottom: 0 }}>
                Phase 1 note: the full AI pipeline (analyze → script → narration →
                subtitles → render) is implemented in later phases. No fake results are
                produced here.
              </p>
            </section>

            {/* ---------- Right: status + history ---------- */}
            <div className="stack">
              {system && <SystemCard system={system} />}

              <section className="card">
                <div className="flex-between">
                  <h2>Pipeline roadmap</h2>
                  <span className="chip-status">
                    <span className="dot" /> interface-ready
                  </span>
                </div>
                <ul className="pipeline">
                  {PIPELINE.map((step, i) => (
                    <li key={step.name}>
                      <span className="num">{i + 1}</span>
                      <span className="name">{step.name}</span>
                      <span className="when">{step.phase}</span>
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
                  ? "No projects yet. Create your first project on the left."
                  : "Loading projects…"}
              </div>
            ) : (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>ID</th>
                      <th>File</th>
                      <th>Language</th>
                      <th>Length</th>
                      <th>Status</th>
                      <th>Progress</th>
                      <th>Created</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {projects.map((project) => (
                      <tr key={project.id}>
                        <td className="mono">{shortId(project.id)}</td>
                        <td className="filename" title={project.original_filename}>
                          {project.original_filename}
                        </td>
                        <td>{LANGUAGE_LABEL[project.language] ?? project.language}</td>
                        <td>{Math.round(project.target_duration_seconds / 60)} min</td>
                        <td>
                          <span className={`tag tag-${project.status}`}>
                            {STATUS_LABEL[project.status] ?? project.status}
                          </span>
                        </td>
                        <td>
                          <span className="progress-bar">
                            <i style={{ width: `${project.progress}%` }} />
                          </span>{" "}
                          <span className="muted">{Math.round(project.progress)}%</span>
                        </td>
                        <td className="muted">{formatWhen(project.created_at)}</td>
                        <td>
                          <button
                            type="button"
                            className="btn-danger"
                            disabled={busyDelete === project.id}
                            onClick={() => void removeProject(project)}
                          >
                            {busyDelete === project.id ? "…" : "Delete"}
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>

          <footer className="footer-note">
            Local AI Video Explainer — Phase 1 foundation. SQLite + FastAPI + FFmpeg architecture;
            no paid APIs, no cloud models, no secrets in source.
          </footer>
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
    {
      label: "Backend",
      value: `${system.application.name} v${system.application.version}`,
    },
    {
      label: "Python",
      value: `${system.python.implementation} ${system.python.version}`,
    },
    {
      label: "SQLite",
      value: system.sqlite.available ? `v${system.sqlite.version} — ready` : "unavailable",
    },
    {
      label: "Database",
      value: system.database.reachable ? "connected & initialized" : "unreachable",
    },
    {
      label: "FFmpeg",
      value: system.ffmpeg.ffmpeg.available
        ? `v${system.ffmpeg.ffmpeg.version ?? "?"} — ready`
        : "not installed (see setup hint)",
    },
    {
      label: "Heavy jobs",
      value: `${system.concurrency.heavy_jobs} at a time`,
    },
    {
      label: "Max upload",
      value: `${system.limits.max_upload_size_mb} MB`,
    },
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
