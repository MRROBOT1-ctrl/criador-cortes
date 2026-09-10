import { useEffect, useState } from "react";
import type { RefObject } from "react";
import { getClipPan, setClipPan } from "../api";
import type { ClipOut, PanKeyframe } from "../types";

function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

interface Props {
  clip: ClipOut;
  sourceVideoRef: RefObject<HTMLVideoElement | null>;
  sourcePreviewFailed: boolean;
  hasStreamerRegion: boolean; // pan manual não é suportado junto com layout de tela dividida
  onApplied: (clip: ClipOut) => void;
}

/**
 * Zoom/crop manual: clica no preview do vídeo ORIGINAL (acima) pra marcar
 * "aqui é onde focar" no instante atual da prévia — sobrescreve o
 * rastreamento automático de rosto (app/services/face_detection.py) pra esse
 * clipe específico. Lista de pontos editável, e um jeito de voltar pro
 * automático (limpar tudo).
 */
export function ManualPanEditor({ clip, sourceVideoRef, sourcePreviewFailed, hasStreamerRegion, onApplied }: Props) {
  const [keyframes, setKeyframes] = useState<PanKeyframe[]>([]);
  const [isManual, setIsManual] = useState(false);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [pendingCx, setPendingCx] = useState<number | null>(null); // último clique, aguardando confirmação do tempo

  const clipDuration = clip.end_time - clip.start_time;

  useEffect(() => {
    let cancelled = false;
    getClipPan(clip.id)
      .then((res) => {
        if (cancelled) return;
        setKeyframes(res.keyframes);
        setIsManual(res.is_manual);
      })
      .catch(() => {
        /* sem pan salvo ainda — segue com lista vazia */
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
  }, [clip.id]);

  function handlePreviewClick(e: React.MouseEvent<HTMLDivElement>) {
    const rect = e.currentTarget.getBoundingClientRect();
    const cx = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    setPendingCx(cx);
  }

  function confirmPendingPoint() {
    const video = sourceVideoRef.current;
    if (pendingCx === null || !video) return;
    const t = Math.min(clipDuration, Math.max(0, video.currentTime - clip.start_time));
    const next = [...keyframes.filter((kf) => Math.abs(kf.t - t) > 0.05), { t, cx: pendingCx }].sort(
      (a, b) => a.t - b.t,
    );
    setKeyframes(next);
    setPendingCx(null);
  }

  function removeKeyframe(t: number) {
    setKeyframes(keyframes.filter((kf) => kf.t !== t));
  }

  async function save(newKeyframes: PanKeyframe[]) {
    setError(null);
    setApplying(true);
    try {
      const updated = await setClipPan(clip.id, newKeyframes);
      setKeyframes(newKeyframes);
      setIsManual(newKeyframes.length > 0);
      onApplied(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao aplicar o zoom manual.");
    } finally {
      setApplying(false);
    }
  }

  if (hasStreamerRegion) {
    return (
      <p className="text-xs text-slate-500">
        Este job usa layout de tela dividida (streamer detectado) — zoom manual ainda não é suportado nesse caso.
      </p>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-slate-500">
        Clique no vídeo original acima pra marcar onde focar no instante atual da prévia, depois confirme abaixo.
        {isManual ? " (zoom manual ativo)" : " (usando detecção automática de rosto)"}
      </p>

      {!sourcePreviewFailed && (
        <div
          onClick={handlePreviewClick}
          className="relative h-6 w-full cursor-crosshair rounded-md border border-dashed border-slate-700 bg-slate-800/50"
          title="Clique pra marcar a posição horizontal de foco"
        >
          {pendingCx !== null && (
            <div
              className="absolute top-0 h-full w-1 bg-amber-400"
              style={{ left: `${pendingCx * 100}%` }}
            />
          )}
        </div>
      )}
      {pendingCx !== null && (
        <button
          type="button"
          onClick={confirmPendingPoint}
          className="self-start rounded-md bg-amber-600 px-2 py-1 text-xs font-medium text-white hover:bg-amber-500"
        >
          Confirmar ponto no tempo atual da prévia ({sourceVideoRef.current ? formatTime(sourceVideoRef.current.currentTime - clip.start_time) : "0:00"})
        </button>
      )}

      {!loading && keyframes.length > 0 && (
        <ul className="flex flex-col gap-1 text-xs text-slate-300">
          {keyframes.map((kf) => (
            <li key={kf.t} className="flex items-center justify-between rounded bg-slate-800 px-2 py-1">
              <span>
                {formatTime(kf.t)} → foco em {(kf.cx * 100).toFixed(0)}%
              </span>
              <button
                type="button"
                onClick={() => removeKeyframe(kf.t)}
                className="text-slate-500 hover:text-red-400"
                aria-label="Remover ponto"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={applying || keyframes.length === 0}
          onClick={() => save(keyframes)}
          className="rounded-md bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {applying ? "Aplicando..." : "Aplicar zoom manual"}
        </button>
        {isManual && (
          <button
            type="button"
            disabled={applying}
            onClick={() => save([])}
            className="rounded-md bg-slate-800 px-3 py-1 text-xs font-semibold text-slate-200 hover:bg-slate-700"
          >
            Voltar pro automático
          </button>
        )}
      </div>
    </div>
  );
}
