import { useEffect, useState } from "react";
import { listJobs } from "../api";
import type { JobSummary, JobStatus } from "../types";
import { Badge, Button, Card, EmptyState } from "./ui";
import type { BadgeVariant } from "./ui";

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

// pending fica "neutral" (era um tom levemente diferente antes — bg-surface-
// active/text-heading — consolidado aqui com o mesmo "neutral" do Badge;
// diferença mínima e só nesse status transitório, ver relatório da Parte 2).
const STATUS_VARIANTS: Record<JobStatus, BadgeVariant> = {
  pending: "neutral",
  running: "accent",
  completed: "success",
  failed: "danger",
};

// Skeleton pulse (Parte 3, item 3b) — mesma forma/grid dos cards de verdade,
// só com barras cinzas pulsando no lugar do conteúdo, enquanto os jobs ainda
// não chegaram da API. `motion-safe:animate-pulse` (built-in do Tailwind):
// com prefers-reduced-motion, some o pulsar e fica só a forma estática — o
// bastante pra indicar "carregando" sem depender de movimento.
function HomeScreenSkeleton() {
  return (
    <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3" aria-hidden="true">
      {[0, 1, 2].map((i) => (
        <Card key={i} padding="sm" className="flex flex-col gap-3 motion-safe:animate-pulse">
          <div className="flex items-start justify-between gap-2">
            <div className="h-4 w-2/3 rounded bg-surface-hover" />
            <div className="h-5 w-16 shrink-0 rounded-full bg-surface-hover" />
          </div>
          <div className="h-3 w-1/3 rounded bg-surface-hover" />
          <div className="h-3 w-1/2 rounded bg-surface-hover" />
        </Card>
      ))}
    </div>
  );
}

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
        <h2 className="text-xl font-semibold text-text">Meus cortes</h2>
        <Button onClick={onNewClip}>+ Novo corte</Button>
      </div>

      {error && (
        <div className="rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
          {error}
        </div>
      )}

      {!jobs && !error && <HomeScreenSkeleton />}

      {jobs && jobs.length === 0 && (
        <EmptyState
          icon="🎬"
          title="Nenhum corte gerado ainda"
          description="Envie seu primeiro vídeo pra gerar cortes verticais automáticos com legenda e hashtags."
          action={<Button onClick={onNewClip}>+ Novo corte</Button>}
        />
      )}

      {jobs && jobs.length > 0 && (
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {jobs.map((job) => (
            <Card
              key={job.id}
              as="button"
              padding="sm"
              onClick={() => onSelectJob(job)}
              className="flex flex-col gap-2 text-left hover:border-border-strong hover:bg-surface-hover"
            >
              <div className="flex items-start justify-between gap-2">
                <span className="line-clamp-2 text-sm font-semibold text-text">
                  {job.video_filename || "Vídeo sem nome"}
                </span>
                <Badge variant={STATUS_VARIANTS[job.status] ?? "neutral"}>
                  {STATUS_LABELS[job.status] ?? job.status}
                </Badge>
              </div>
              <p className="text-xs text-text-muted">{formatDate(job.created_at)}</p>
              <div className="mt-1 flex items-center justify-between text-xs text-text-subtle">
                <span>
                  {job.clip_count} corte{job.clip_count === 1 ? "" : "s"}
                </span>
                {job.video_duration != null && <span>{Math.round(job.video_duration)}s de vídeo original</span>}
              </div>
            </Card>
          ))}
        </div>
      )}
    </div>
  );
}
