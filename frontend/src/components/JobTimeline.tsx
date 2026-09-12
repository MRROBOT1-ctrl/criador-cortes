import { useEffect, useMemo, useState } from "react";
import { clipFileUrl, compileClips, compiledVideoUrl, reorderClips } from "../api";
import type { ClipOut } from "../types";
import { Button, buttonClasses, cn } from "./ui";

interface Props {
  jobId: string;
  clips: ClipOut[];
  onReordered: (clips: ClipOut[]) => void;
}

// Timeline compacta do job inteiro: ver os clipes em ordem, reordenar via
// drag-and-drop nativo (sem lib nova) e gerar um vídeo único juntando os
// clipes marcados, na ordem em que aparecem aqui.
export function JobTimeline({ jobId, clips, onReordered }: Props) {
  // Ordem local exibida na tira — sincronizada com `clips` (fonte da
  // verdade vinda do backend) sempre que ela mudar de fora (ex: outro clipe
  // foi editado/recortado e a lista foi recarregada).
  const [order, setOrder] = useState<ClipOut[]>(() => [...clips].sort((a, b) => a.index - b.index));
  const [included, setIncluded] = useState<Set<string>>(() => new Set(clips.map((c) => c.id)));
  const [dragId, setDragId] = useState<string | null>(null);
  const [reordering, setReordering] = useState(false);
  const [reorderError, setReorderError] = useState<string | null>(null);
  const [compiling, setCompiling] = useState(false);
  const [compileError, setCompileError] = useState<string | null>(null);
  // Muda a cada compilado novo pra forçar o <video> a recarregar (mesma URL,
  // conteúdo novo — o navegador cacheia por padrão).
  const [compiledVersion, setCompiledVersion] = useState(0);
  const [hasCompiled, setHasCompiled] = useState(false);

  useEffect(() => {
    setOrder([...clips].sort((a, b) => a.index - b.index));
    // Default "todos incluídos" sempre que a lista de clipes do job mudar de
    // fora (ex: carregou a tela) — simples e previsível; se o usuário já
    // tinha desmarcado algum antes de um reload isso não é preservado, mas
    // essa tela não recarrega os clipes sozinha durante o uso normal.
    setIncluded(new Set(clips.map((c) => c.id)));
  }, [clips]);

  const includedCount = useMemo(() => order.filter((c) => included.has(c.id)).length, [order, included]);

  async function persistOrder(next: ClipOut[]) {
    setOrder(next);
    setReordering(true);
    setReorderError(null);
    try {
      const updated = await reorderClips(jobId, next.map((c) => c.id));
      onReordered(updated);
    } catch (err) {
      setReorderError(err instanceof Error ? err.message : "Falha ao salvar a nova ordem.");
    } finally {
      setReordering(false);
    }
  }

  function handleDrop(targetId: string) {
    if (!dragId || dragId === targetId) return;
    const fromIndex = order.findIndex((c) => c.id === dragId);
    const toIndex = order.findIndex((c) => c.id === targetId);
    if (fromIndex === -1 || toIndex === -1) return;

    const next = [...order];
    const [moved] = next.splice(fromIndex, 1);
    next.splice(toIndex, 0, moved);
    void persistOrder(next);
    setDragId(null);
  }

  function toggleIncluded(clipId: string) {
    setIncluded((current) => {
      const next = new Set(current);
      if (next.has(clipId)) {
        next.delete(clipId);
      } else {
        next.add(clipId);
      }
      return next;
    });
  }

  async function handleCompile() {
    const selectedIds = order.filter((c) => included.has(c.id)).map((c) => c.id);
    if (selectedIds.length === 0) return;
    setCompiling(true);
    setCompileError(null);
    try {
      await compileClips(jobId, selectedIds);
      setHasCompiled(true);
      setCompiledVersion((v) => v + 1);
    } catch (err) {
      setCompileError(err instanceof Error ? err.message : "Falha ao gerar o vídeo compilado.");
    } finally {
      setCompiling(false);
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-xl border border-border bg-surface/60 p-4">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-semibold text-text">
          Timeline do job — arraste pra reordenar
        </h3>
        {reordering && <span className="text-xs text-text-muted">Salvando ordem...</span>}
      </div>

      {reorderError && (
        <div className="rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-xs text-danger-text">
          {reorderError}
        </div>
      )}

      <div className="flex gap-3 overflow-x-auto pb-1">
        {order.map((clip, position) => {
          const isIncluded = included.has(clip.id);
          const isDragging = dragId === clip.id;
          return (
            <div
              key={clip.id}
              draggable
              onDragStart={() => setDragId(clip.id)}
              onDragOver={(e) => e.preventDefault()}
              onDrop={() => handleDrop(clip.id)}
              onDragEnd={() => setDragId(null)}
              className={`flex w-28 shrink-0 cursor-grab flex-col gap-1 rounded-lg border p-2 active:cursor-grabbing ${
                isDragging ? "border-accent-hover opacity-50" : "border-border-strong bg-bg"
              } ${isIncluded ? "" : "opacity-50"}`}
              title="Arraste para reordenar"
            >
              <video
                src={clipFileUrl(clip.id)}
                muted
                className="aspect-[9/16] w-full rounded bg-black object-cover"
              />
              <div className="flex items-center justify-between gap-1 text-[11px] text-text-secondary">
                <span>#{position + 1}</span>
                <label className="flex items-center gap-1">
                  <input
                    type="checkbox"
                    checked={isIncluded}
                    onChange={() => toggleIncluded(clip.id)}
                    className="h-3 w-3"
                  />
                  incluir
                </label>
              </div>
            </div>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Button loading={compiling} disabled={includedCount === 0} onClick={handleCompile}>
          {compiling ? "Gerando vídeo compilado..." : `Gerar vídeo compilado (${includedCount})`}
        </Button>
        {compileError && <span className="text-xs text-danger-text-strong">{compileError}</span>}
      </div>

      {hasCompiled && (
        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <span className="text-xs font-semibold text-text-secondary">Vídeo compilado:</span>
          <video
            key={compiledVersion}
            src={`${compiledVideoUrl(jobId)}?v=${compiledVersion}`}
            controls
            className="mx-auto aspect-[9/16] w-full max-w-56 rounded-lg bg-black"
          />
          <a
            href={`${compiledVideoUrl(jobId)}?v=${compiledVersion}`}
            download
            className={cn("self-start", buttonClasses("secondary", "sm"))}
          >
            Baixar compilado
          </a>
        </div>
      )}
    </div>
  );
}
