import { useEffect, useState } from "react";
import { getClipCuts, setClipCuts } from "../api";
import type { ClipOut, CutSegment } from "../types";

interface RemovedRange {
  id: number; // chave estável pra lista do React — não usar índice (some/reordena ao excluir)
  start: number;
  end: number;
}

function formatTime(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${m}:${sec.toFixed(1).padStart(4, "0")}`;
}

// A API (Clip.cut_segments) trabalha com os trechos MANTIDOS — mais simples
// de validar/remapear no backend (ver remap_time_through_cuts). Pro usuário,
// porém, é bem mais natural pensar em "quais trechos eu quero jogar fora"
// (o silêncio, a fala descartada) do que ficar re-listando tudo que sobra
// em volta. A UI trabalha só com "trechos removidos" e converte pros dois
// lados (a lista completa de segmentos mantidos é o COMPLEMENTO da lista de
// removidos dentro de [0, duration]).
function sortAndMergeRanges(ranges: { start: number; end: number }[]): { start: number; end: number }[] {
  const sorted = ranges
    .filter((r) => r.end > r.start)
    .map((r) => ({ ...r }))
    .sort((a, b) => a.start - b.start);
  const merged: { start: number; end: number }[] = [];
  for (const r of sorted) {
    const last = merged[merged.length - 1];
    if (last && r.start <= last.end) {
      last.end = Math.max(last.end, r.end);
    } else {
      merged.push(r);
    }
  }
  return merged;
}

function removedToKeepSegments(removed: { start: number; end: number }[], duration: number): CutSegment[] {
  const merged = sortAndMergeRanges(removed).map((r) => ({
    start: Math.max(0, r.start),
    end: Math.min(duration, r.end),
  }));
  const keep: CutSegment[] = [];
  let cursor = 0;
  for (const r of merged) {
    if (r.start > cursor) keep.push([cursor, r.start]);
    cursor = Math.max(cursor, r.end);
  }
  if (cursor < duration) keep.push([cursor, duration]);
  return keep;
}

function keepSegmentsToRemoved(keep: CutSegment[], duration: number): { start: number; end: number }[] {
  if (keep.length === 0) return [];
  const removed: { start: number; end: number }[] = [];
  let cursor = 0;
  for (const [s, e] of keep) {
    if (s > cursor) removed.push({ start: cursor, end: s });
    cursor = Math.max(cursor, e);
  }
  if (cursor < duration) removed.push({ start: cursor, end: duration });
  return removed;
}

// Espelha _is_noop_keep_segments do backend (video_cutter.py): sem cortes
// internos de verdade quando a lista tá vazia ou é um único segmento
// cobrindo o clipe inteiro.
function isNoopKeepSegments(keep: CutSegment[], duration: number): boolean {
  if (keep.length === 0) return true;
  if (keep.length === 1) {
    const [s, e] = keep[0];
    return s <= 0.01 && e >= duration - 0.01;
  }
  return false;
}

let nextId = 1;

interface Props {
  clip: ClipOut;
  onApplied: (clip: ClipOut) => void;
}

/**
 * Remoção de trechos do MEIO do clipe (um silêncio, uma fala descartada) —
 * diferente do ajuste de início/fim (TimelineScrubber acima), aqui o
 * resultado final concatena os pedaços que sobram, sem buraco no meio (ver
 * PUT /clips/{id}/cuts no backend). A UI edita a lista de trechos REMOVIDOS;
 * a lista de trechos MANTIDOS (o que a API espera) é só o complemento dela.
 */
export function CutSegmentsEditor({ clip, onApplied }: Props) {
  const clipDuration = clip.end_time - clip.start_time;
  const [removed, setRemoved] = useState<RemovedRange[]>([]);
  const [isActive, setIsActive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getClipCuts(clip.id)
      .then((res) => {
        if (cancelled) return;
        const asRemoved = keepSegmentsToRemoved(res.keep_segments, clipDuration).map((r) => ({
          id: nextId++,
          ...r,
        }));
        setRemoved(asRemoved);
        setIsActive(res.is_active);
      })
      .catch(() => {
        /* sem cortes salvos ainda — segue com lista vazia */
      })
      .finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.id]);

  function addRange() {
    // Sugere um trecho pequeno no meio do que ainda não foi marcado, só pra
    // dar um ponto de partida razoável — o usuário ajusta os números depois.
    const defaultStart = Math.min(clipDuration, removed.reduce((max, r) => Math.max(max, r.end), 0));
    const defaultEnd = Math.min(clipDuration, defaultStart + 2);
    setRemoved([...removed, { id: nextId++, start: defaultStart, end: defaultEnd }]);
  }

  function updateRange(id: number, field: "start" | "end", value: number) {
    setRemoved(removed.map((r) => (r.id === id ? { ...r, [field]: value } : r)));
  }

  function removeRange(id: number) {
    setRemoved(removed.filter((r) => r.id !== id));
  }

  const keepSegments = removedToKeepSegments(removed, clipDuration);
  const keepPreview =
    keepSegments.length === 0
      ? "o clipe inteiro será removido — adicione pelo menos um trecho pra manter"
      : keepSegments.map(([s, e]) => `${formatTime(s)}–${formatTime(e)}`).join(", ");

  async function apply() {
    setError(null);
    setApplying(true);
    try {
      const updated = await setClipCuts(clip.id, keepSegments);
      setIsActive(!isNoopKeepSegments(keepSegments, clipDuration));
      onApplied(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao aplicar os cortes.");
    } finally {
      setApplying(false);
    }
  }

  async function clearCuts() {
    setError(null);
    setApplying(true);
    try {
      const updated = await setClipCuts(clip.id, []);
      setRemoved([]);
      setIsActive(false);
      onApplied(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao remover os cortes.");
    } finally {
      setApplying(false);
    }
  }

  if (loading) {
    return <p className="text-xs text-slate-500">Carregando cortes...</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-slate-500">
        Marque trechos do MEIO do clipe pra remover (um silêncio, uma fala descartada) — o resultado final
        concatena o que sobrar, sem o buraco no meio.
        {isActive ? " (cortes internos ativos)" : " (nenhum corte interno configurado)"}
      </p>

      {removed.length > 0 && (
        <ul className="flex flex-col gap-2">
          {removed.map((r) => (
            <li key={r.id} className="flex items-center gap-2 rounded bg-slate-800 px-2 py-1.5">
              <span className="text-xs text-slate-500">remover</span>
              <input
                type="number"
                step="0.1"
                min={0}
                max={clipDuration}
                value={r.start}
                onChange={(e) => updateRange(r.id, "start", Number(e.target.value))}
                className="w-20 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-100"
                aria-label="Início do trecho removido (s)"
              />
              <span className="text-xs text-slate-500">até</span>
              <input
                type="number"
                step="0.1"
                min={0}
                max={clipDuration}
                value={r.end}
                onChange={(e) => updateRange(r.id, "end", Number(e.target.value))}
                className="w-20 rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-100"
                aria-label="Fim do trecho removido (s)"
              />
              <span className="text-xs text-slate-500">s</span>
              <button
                type="button"
                onClick={() => removeRange(r.id)}
                className="ml-auto text-slate-500 hover:text-red-400"
                aria-label="Descartar este trecho removido"
              >
                ✕
              </button>
            </li>
          ))}
        </ul>
      )}

      <button
        type="button"
        onClick={addRange}
        disabled={applying}
        className="self-start rounded-md bg-slate-800 px-3 py-1 text-xs font-semibold text-slate-200 hover:bg-slate-700 disabled:cursor-not-allowed disabled:opacity-50"
      >
        + remover um trecho
      </button>

      <p className="text-xs text-slate-400">
        <span className="font-medium text-slate-300">mantendo:</span> {keepPreview}
      </p>

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={applying || keepSegments.length === 0}
          onClick={apply}
          className="rounded-md bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {applying ? "Aplicando..." : "Aplicar cortes"}
        </button>
        {isActive && (
          <button
            type="button"
            disabled={applying}
            onClick={clearCuts}
            className="rounded-md bg-slate-800 px-3 py-1 text-xs font-semibold text-slate-200 hover:bg-slate-700"
          >
            Remover todos os cortes internos
          </button>
        )}
      </div>
    </div>
  );
}
