import type { JobOut, JobStatus } from "../types";
import { Badge, Card } from "./ui";
import type { BadgeVariant } from "./ui";

// Mesmo mapa de HomeScreen.tsx — os dois tinham essa duplicação exata antes
// (um Record<JobStatus, string> de classes CSS cada um) — ver Badge.tsx.
const STATUS_VARIANTS: Record<JobStatus, BadgeVariant> = {
  pending: "neutral",
  running: "accent",
  completed: "success",
  failed: "danger",
};

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
    <Card className="mx-auto flex w-full max-w-xl flex-col gap-4">
      <div>
        <h2 className="text-xl font-semibold text-text">Processando vídeo</h2>
        <p className="mt-1 text-sm text-text-muted">Job {job.id}</p>
      </div>

      <div>
        <div className="mb-1.5 flex items-center justify-between text-sm">
          <span className="font-medium text-text-secondary">{phaseLabel}</span>
          <span className="text-text-muted">{job.progress}%</span>
        </div>
        <div className="h-2.5 w-full overflow-hidden rounded-full bg-surface-hover">
          <div
            className="h-full rounded-full bg-accent motion-safe:transition-all motion-safe:duration-500"
            style={{ width: `${Math.min(100, Math.max(0, job.progress))}%` }}
          />
        </div>
      </div>

      <div className="flex items-center gap-2 text-sm">
        <span className="text-text-muted">Status:</span>
        <Badge variant={STATUS_VARIANTS[job.status] ?? "neutral"}>{job.status}</Badge>
      </div>

      {job.status === "failed" && job.error_message && (
        <div className="rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
          <p className="font-semibold">Erro no processamento:</p>
          <pre className="mt-1 max-h-40 overflow-auto whitespace-pre-wrap font-mono text-xs">{job.error_message}</pre>
        </div>
      )}

      <div>
        <p className="mb-1.5 text-sm font-medium text-text-secondary">Logs</p>
        <pre className="log-scroll h-40 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-bg p-3 font-mono text-xs text-text-muted">
          {job.logs || "Sem logs ainda."}
        </pre>
      </div>
    </Card>
  );
}
