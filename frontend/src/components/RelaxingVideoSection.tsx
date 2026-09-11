import { useEffect, useRef, useState } from "react";
import { addRelaxingVideo, deleteRelaxingVideo, getRelaxingVideo, listRelaxingVideos } from "../api";
import type { RelaxingVideoOut } from "../types";

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
    <div className="flex flex-col gap-3 rounded-lg border border-slate-800 p-3">
      <label className="flex items-center gap-2 text-sm font-medium text-slate-300">
        <input type="checkbox" checked={enabled} onChange={(e) => onEnabledChange(e.target.checked)} />
        Vídeo relaxante de fundo em trechos de narração
      </label>
      <p className="text-xs text-slate-500">
        Quando o vídeo não tem streamer nem ninguém rastreável (só narração), usa um vídeo de fundo tipo
        parkour/slime em vez do corte central vazio. Desmarcado = "sem janela", sempre corte central simples.
      </p>

      {enabled && (
        <div className="flex flex-col gap-2 border-t border-slate-800 pt-3">
          <div className="flex gap-2">
            <input
              type="url"
              placeholder="Link de um vídeo (YouTube etc.) pra adicionar à biblioteca"
              value={newUrl}
              onChange={(e) => setNewUrl(e.target.value)}
              className="flex-1 rounded-md border border-slate-700 bg-slate-800 px-2 py-1.5 text-xs text-slate-100 placeholder:text-slate-500"
            />
            <button
              type="button"
              onClick={handleAdd}
              disabled={adding || !newUrl.trim()}
              className="rounded-md bg-indigo-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {adding ? "Baixando..." : "Adicionar"}
            </button>
          </div>
          {error && <p className="text-xs text-red-400">{error}</p>}

          {videos === null ? (
            <p className="text-xs text-slate-500">Carregando biblioteca...</p>
          ) : videos.length === 0 ? (
            <p className="text-xs text-slate-500">
              Nenhum vídeo relaxante ainda — sem um na biblioteca, trechos de narração usam o corte central
              normal mesmo com isso marcado.
            </p>
          ) : (
            <ul className="flex flex-col gap-1">
              {videos.map((v) => (
                <li key={v.id} className="flex flex-col gap-0.5 rounded bg-slate-800 px-2 py-1 text-xs text-slate-300">
                  <div className="flex items-center justify-between">
                    <span className="truncate">
                      {v.status === "downloading"
                        ? "Baixando..."
                        : `${v.title || v.id} (${formatDuration(v.duration)})`}
                    </span>
                    <button
                      type="button"
                      onClick={() => handleDelete(v.id)}
                      className="ml-2 shrink-0 text-slate-500 hover:text-red-400"
                      aria-label="Remover"
                    >
                      ✕
                    </button>
                  </div>
                  {v.status === "failed" && (
                    <span className="text-red-400">{v.error_message || "Falha ao baixar esse vídeo."}</span>
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
