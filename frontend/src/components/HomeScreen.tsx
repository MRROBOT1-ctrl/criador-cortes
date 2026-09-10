import { useEffect, useState } from "react";
import { listJobs } from "../api";
import type { JobSummary, JobStatus } from "../types";

interface Props {
  onNewClip: () => void;
  onSelectJob: (job: JobSummary) => void;
}

const STATUS_LABELS: Record<JobStatus, string> = {
  pending: "Pendente",
  running: "Processando",
  completed: "Concluído",
  failed: "Falhou",
};

const STATUS_STYLES: Record<JobStatus, string> = {
  pending: "bg-slate-700 text-slate-200",
  running: "bg-indigo-900 text-indigo-200",
  completed: "bg-emerald-900 text-emerald-200",
  failed: "bg-red-900 text-red-200",
};

function formatDate(iso: string): string {
  return new Date(iso).toLocaleString("pt-BR", {
    day: "2-digit",
    month: "2-digit",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function HomeScreen({ onNewClip, onSelectJob }: Props) {
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    listJobs()
      .then((data) => {
        if (!cancelled) setJobs(data);
      })
      .catch((err) => {
        if (!cancelled) setError(err instanceof Error ? err.message : "Falha ao carregar os cortes.");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-slate-100">Meus cortes</h2>
        <button
          type="button"
          onClick={onNewClip}
          className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500"
        >
          + Novo corte
        </button>
      </div>

      {error && (
        <div className="rounded-lg border border-red-800 bg-red-950/50 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      {!jobs && !error && <p className="text-sm text-slate-400">Carregando...</p>}

      {jobs && jobs.length === 0 && (
        <p className="text-sm text-slate-400">
          Nenhum corte gerado ainda. Clique em &quot;+ Novo corte&quot; para enviar seu primeiro vídeo.
        </p>
      )}

      {jobs && jobs.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {jobs.map((job) => (
            <button
              key={job.id}
              type="button"
              onClick={() => onSelectJob(job)}
              className="flex flex-col gap-2 rounded-xl border border-slate-800 bg-slate-900 p-4 text-left shadow-lg transition hover:border-slate-700 hover:bg-slate-800"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="line-clamp-2 text-sm font-semibold text-slate-100">
                  {job.video_filename || "Vídeo sem nome"}
                </span>
                <span
                  className={`shrink-0 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
                    STATUS_STYLES[job.status] ?? STATUS_STYLES.pending
                  }`}
                >
                  {STATUS_LABELS[job.status] ?? job.status}
                </span>
              </div>
              <p className="text-xs text-slate-400">{formatDate(job.created_at)}</p>
              <div className="mt-1 flex items-center justify-between text-xs text-slate-500">
                <span>
                  {job.clip_count} corte{job.clip_count === 1 ? "" : "s"}
                </span>
                {job.video_duration != null && <span>{Math.round(job.video_duration)}s de vídeo original</span>}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
