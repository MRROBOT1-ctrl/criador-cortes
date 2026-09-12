import { useEffect, useState } from "react";
import { listClips } from "./api";
import { ClipEditorScreen } from "./components/ClipEditorScreen";
import { ClipsList } from "./components/ClipsList";
import { HomeScreen } from "./components/HomeScreen";
import { ProgressPanel } from "./components/ProgressPanel";
import { UploadForm } from "./components/UploadForm";
import { useJobStatus } from "./hooks/useJobStatus";
import { Button } from "./components/ui";
import type { ClipOut, JobSummary, UploadResponse } from "./types";

// Fluxo de uma tela só: home (lista de jobs) -> upload -> progresso -> resultado.
// "editing" é uma tela cheia separada (não mais um modal por cima de "clips")
// — ver ClipEditorScreen.tsx. Sem react-router — não há necessidade de rotas
// pra esses estados.
type Stage = "home" | "upload" | "progress" | "clips" | "editing";

export default function App() {
  const [stage, setStage] = useState<Stage>("home");
  const [jobId, setJobId] = useState<string | null>(null);
  const [clips, setClips] = useState<ClipOut[] | null>(null);
  const [clipsError, setClipsError] = useState<string | null>(null);
  const [editingClipId, setEditingClipId] = useState<string | null>(null);

  const job = useJobStatus(stage === "progress" ? jobId : null);
  const editingClip = clips?.find((c) => c.id === editingClipId) ?? null;

  useEffect(() => {
    if (job?.status === "completed" && jobId) {
      listClips(jobId)
        .then((data) => {
          setClips(data);
          setStage("clips");
        })
        .catch((err) => setClipsError(err instanceof Error ? err.message : "Falha ao carregar os clipes."));
    }
  }, [job?.status, jobId]);

  // Clipe da edição sumiu (ex: apagado em outra aba) — volta pra lista em
  // vez de deixar a tela de edição travada sem nada pra mostrar.
  useEffect(() => {
    if (stage === "editing" && !editingClip) {
      setStage("clips");
    }
  }, [stage, editingClip]);

  function handleUploaded(response: UploadResponse) {
    setJobId(response.job.id);
    setClips(null);
    setClipsError(null);
    setStage("progress");
  }

  function handleClipUpdated(updated: ClipOut) {
    setClips((current) => current?.map((c) => (c.id === updated.id ? updated : c)) ?? current);
  }

  function handleEditClip(clip: ClipOut) {
    setEditingClipId(clip.id);
    setStage("editing");
  }

  function handleCloseEditor() {
    setEditingClipId(null);
    setStage("clips");
  }

  function handleClipsReordered(updated: ClipOut[]) {
    // Vem já ordenado por index do PUT /jobs/{id}/clips/order — troca a
    // lista inteira em vez de mesclar campo a campo, mais simples aqui.
    setClips(updated);
  }

  function handleReset() {
    setJobId(null);
    setClips(null);
    setClipsError(null);
    setStage("upload");
  }

  function handleGoHome() {
    setJobId(null);
    setClips(null);
    setClipsError(null);
    setStage("home");
  }

  function handleSelectJob(selected: JobSummary) {
    setJobId(selected.id);
    setClipsError(null);
    if (selected.status === "completed") {
      // Já concluído: pula direto pra tela de clipes em vez de passar pelo
      // progresso (que não faria sentido mostrar de novo pra um job pronto).
      listClips(selected.id)
        .then((data) => {
          setClips(data);
          setStage("clips");
        })
        .catch((err) => setClipsError(err instanceof Error ? err.message : "Falha ao carregar os clipes."));
    } else {
      // pending | running | failed -> ProgressPanel já trata os 3 casos
      // (barra de progresso, ou erro + botão de voltar quando failed).
      setClips(null);
      setStage("progress");
    }
  }

  return (
    <div className="min-h-screen bg-bg px-4 py-10">
      <header className="mx-auto mb-8 max-w-6xl">
        {stage !== "home" && (
          <Button variant="ghost" className="mb-3" onClick={handleGoHome}>
            ← Meus cortes
          </Button>
        )}
        <h1 className="text-2xl font-bold text-text">VideoCutterAI</h1>
        <p className="mt-1 text-sm text-text-muted">
          Gere cortes verticais automáticos com legenda e hashtags a partir de um vídeo longo.
        </p>
      </header>

      {/* `key={stage}` força remontar a cada troca de estágio — é isso que
          reinicia a animação de entrada (fade + slide-up leve) toda vez,
          em vez de rodar só na primeira montagem do app. */}
      <main key={stage} className="animate-stage-enter">
        {stage === "home" && <HomeScreen onNewClip={() => setStage("upload")} onSelectJob={handleSelectJob} />}

        {stage === "upload" && <UploadForm onUploaded={handleUploaded} />}

        {stage === "progress" && (
          <div className="flex flex-col items-center gap-6">
            {job ? (
              <ProgressPanel job={job} />
            ) : (
              <p className="text-sm text-text-muted">Conectando ao status do job...</p>
            )}
            {job?.status === "failed" && (
              <Button variant="secondary" onClick={handleReset}>
                Tentar com outro vídeo
              </Button>
            )}
            {clipsError && (
              <div className="w-full max-w-xl rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
                {clipsError}
              </div>
            )}
          </div>
        )}

        {stage === "clips" && jobId && clips && (
          <ClipsList
            jobId={jobId}
            clips={clips}
            onEditClip={handleEditClip}
            onClipsReordered={handleClipsReordered}
            onReset={handleReset}
          />
        )}

        {stage === "editing" && editingClip && (
          <ClipEditorScreen clip={editingClip} onBack={handleCloseEditor} onUpdated={handleClipUpdated} />
        )}
      </main>
    </div>
  );
}
