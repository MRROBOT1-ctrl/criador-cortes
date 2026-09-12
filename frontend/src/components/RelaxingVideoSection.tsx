import { useEffect, useRef, useState } from "react";
import { addRelaxingVideo, deleteRelaxingVideo, getRelaxingVideo, listRelaxingVideos } from "../api";
import type { RelaxingVideoOut } from "../types";
import { Button, cn, FOCUS_VISIBLE_RING, MOTION_SAFE_TRANSITION } from "./ui";

// Intervalo do polling de status (ver POST /relaxing-videos, que retorna
// antes do download terminar) — 3s é frequente o suficiente pra não parecer
// travado, sem martelar o backend à toa.
const POLL_INTERVAL_MS = 3000;

interface Props {
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
}

function formatDuration(seconds: number | null): string {
  if (seconds == null) return "?";
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

/**
 * Toggle "sem janela" + biblioteca de vídeos relaxantes de fundo (parkour/
 * slime/satisfying etc.) — usados como fallback visual quando o vídeo é só
 * narração (sem streamer, sem rosto rastreável). Desligar o toggle é o
 * "sem janela": nesse caso o clipe sempre usa o crop central simples de
 * sempre, mesmo sem achar ninguém pra focar.
 */
export function RelaxingVideoSection({ enabled, onEnabledChange }: Props) {
  const [videos, setVideos] = useState<RelaxingVideoOut[] | null>(null);
  const [newUrl, setNewUrl] = useState("");
  const [adding, setAdding] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!enabled || videos !== null) return;
    listRelaxingVideos()
      .then(setVideos)
      .catch(() => setVideos([]));
  }, [enabled, videos]);

  // Polling de status pros vídeos ainda "downloading" (ver POST
  // /relaxing-videos, que retorna antes do download terminar de verdade —
  // o worker Celery baixa em background e atualiza o registro depois).
  // `videosRef` deixa o intervalo sempre ler o estado MAIS RECENTE sem
  // precisar recriar o setInterval a cada atualização de `videos` (senão o
  // timer reiniciaria toda vez que um item mudasse de status, atrasando o
  // polling dos outros que ainda estão pendentes).
  const videosRef = useRef<RelaxingVideoOut[] | null>(null);
  useEffect(() => {
    videosRef.current = videos;
  }, [videos]);

  useEffect(() => {
    const interval = setInterval(async () => {
      const pending = (videosRef.current ?? []).filter((v) => v.status === "downloading");
      for (const v of pending) {
        try {
          const updated = await getRelaxingVideo(v.id);
          setVideos((current) => (current ?? []).map((item) => (item.id === updated.id ? updated : item)));
        } catch {
          // 404 — apagado enquanto baixava (por essa aba ou outra sessão).
          // Some da lista local também: não faz sentido continuar mostrando
          // "Baixando..." pra um item que não existe mais.
          setVideos((current) => (current ?? []).filter((item) => item.id !== v.id));
        }
      }
    }, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, []);

  async function handleAdd() {
    const url = newUrl.trim();
    if (!url) return;
    setAdding(true);
    setError(null);
    try {
      // Retorna na hora com status="downloading" — o polling acima assume
      // a partir daqui e atualiza esse item quando o download terminar.
      const video = await addRelaxingVideo(url);
      setVideos((current) => [video, ...(current ?? [])]);
      setNewUrl("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao iniciar o download desse vídeo.");
    } finally {
      setAdding(false);
    }
  }

  async function handleDelete(id: string) {
    setVideos((current) => (current ?? []).filter((v) => v.id !== id));
    try {
      await deleteRelaxingVideo(id);
    } catch {
      // Falha silenciosa aqui não é grave — na pior das hipóteses a lista
      // volta a mostrar esse item na próxima vez que a seção for aberta.
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-3">
      <label className="flex items-center gap-2 text-sm font-medium text-text-secondary">
        <input type="checkbox" checked={enabled} onChange={(e) => onEnabledChange(e.target.checked)} />
        Vídeo relaxante de fundo em trechos de narração
      </label>
      <p className="text-xs text-text-subtle">
        Quando o vídeo não tem streamer nem ninguém rastreável (só narração), usa um vídeo de fundo tipo
        parkour/slime em vez do corte central vazio. Desmarcado = "sem janela", sempre corte central simples.
      </p>

      {enabled && (
        <div className="flex flex-col gap-2 border-t border-border pt-3">
          <div className="flex gap-2">
            {/* Input.tsx não é usado aqui de propósito: esse campo é menor
                (px-2 py-1.5 text-xs) que o padrão do Input (px-3 py-2 text-sm)
                — é uma variação de densidade pro widget compacto de
                biblioteca, não uma cópia do padrão de formulário grande. */}
            <input
              type="url"
              placeholder="Link de um vídeo (YouTube etc.) pra adicionar à biblioteca"
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              className={cn(
                "flex-1 rounded-md border border-border-strong bg-surface-hover px-2 py-1.5 text-xs text-text placeholder:text-text-subtle",
                MOTION_SAFE_TRANSITION,
                "outline-none focus:border-accent focus:[--tw-outline-style:solid] focus:outline-2 focus:outline-accent focus:outline-offset-2",
              )}
            />
            <Button size="sm" loading={adding} disabled={!newUrl.trim()} onClick={handleAdd}>
              {adding ? "Baixando..." : "Adicionar"}
            </Button>
          </div>
          {error && <p className="text-xs text-danger-text-strong">{error}</p>}

          {videos === null ? (
            <p className="text-xs text-text-subtle">Carregando biblioteca...</p>
          ) : videos.length === 0 ? (
            <p className="text-xs text-text-subtle">
              Nenhum vídeo relaxante ainda — sem um na biblioteca, trechos de narração usam o corte central
              normal mesmo com isso marcado.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {videos.map((v) => (
                <li key={v.id} className="flex flex-col gap-0.5 rounded bg-surface-hover px-2 py-1 text-xs text-text-secondary">
                  <div className="flex items-center justify-between">
                    <span className="truncate">
                      {v.status === "downloading"
                        ? "Baixando..."
                        : `${v.title || v.id} (${formatDuration(v.duration)})`}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleDelete(v.id)}
                      className={cn(
                        "ml-2 shrink-0 rounded text-text-subtle hover:text-danger-text-strong",
                        MOTION_SAFE_TRANSITION,
                        FOCUS_VISIBLE_RING,
                        "active:brightness-90 motion-safe:active:scale-[0.98]",
                      )}
                      aria-label="Remover"
                    >
                      ✕
                    </button>
                  </div>
                  {v.status === "failed" && (
                    <span className="text-danger-text-strong">{v.error_message || "Falha ao baixar esse vídeo."}</span>
                  )}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
