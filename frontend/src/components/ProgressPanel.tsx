import type { JobOut } from "../types";

const PHASE_LABELS: Record<string, string> = {
  extracting_audio: "Extraindo áudio",
  transcribing: "Transcrevendo áudio",
  analyzing: "Analisando picos de áudio e palavras-chave",
  selecting: "Selecionando melhores momentos",
  cutting: "Cortando e renderizando clipes",
  done: "Concluído",
};

interface Props {
  job: JobOut;
}

export function ProgressPanel({ job }: Props) {
  const phaseLabel = job.current_phase ? PHASE_LABELS[job.current_phase] ?? job.current_phase : "Aguardando início...";

  return (
    <div className="mx-auto flex w-full max-w-xl flex-col gap-4 rounded-xl border border-slate-800 bg-slate-900 p-6 shadow-lg">
      <div>
        <h2 className="text-lg font-semibold text-slate-100">Processando vídeo</h2>
        <p className="mt-1 text-sm text-slate-400">Job {job.id}</p>
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between text-sm">
          <span className="font-medium text-slate-300">{phaseLabel}</span>
          <span className="text-slate-400">{job.progress}%</span>
        </div>
        <div className="h-2.5 w-full overflow-hidden rounded-full bg-slate-800">
          <div
            className="h-full rounded-full bg-indigo-600 transition-all duration-500"
            style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
          />
        </div>
      </div>

      <div className="flex items-center gap-2 text-sm">
        <span className="text-slate-400">Status:</span>
        <StatusBadge status={job.status} />
      </div>

      {job.status === "failed" && job.error_message && (
        <div className="rounded-lg border border-red-800 bg-red-950/50 px-3 py-2 text-sm text-red-300">
          <p className="font-semibold">Erro no processamento:</p>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs">{job.error_message}</pre>
        </div>
      )}

      <div>
        <p className="mb-1.5 text-sm font-medium text-slate-300">Logs</p>
        <pre className="log-scroll h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-slate-800 bg-slate-950 p-3 font-mono text-xs text-slate-400">
          {job.logs || "Sem logs ainda."}
        </pre>
      </div>
    </div>
  );
}

function StatusBadge({ status }: { status: JobOut["status"] }) {
  const styles: Record<string, string> = {
    pending: "bg-slate-700 text-slate-200",
    running: "bg-indigo-900 text-indigo-200",
    completed: "bg-emerald-900 text-emerald-200",
    failed: "bg-red-900 text-red-200",
  };
  return (
    <span className={`rounded-full px-2.5 py-0.5 text-xs font-semibold ${styles[status] ?? styles.pending}`}>
      {status}
    </span>
  );
}
