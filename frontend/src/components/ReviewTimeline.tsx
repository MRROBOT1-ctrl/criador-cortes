import { useRef, useState } from "react";
import type { KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { snapTime } from "../lib/snap";
import type { SnapEdge, SnapWord } from "../lib/snap";
import type { ClipDraft, PanPointAbs, ProfanityRange, TimeRange } from "../types";
import { cn, FOCUS_VISIBLE_RING, MOTION_SAFE_TRANSITION } from "./ui";

// Duração mínima do corte — a mesma do contrato (`end − start ≥ 0.5`), pra
// a tela não deixar montar um rascunho que o backend recusaria com 400.
const MIN_RANGE_SECONDS = 0.5;

// Arrastos menores que isso na trilha Removido/Mudo contam como clique
// (seleciona/desseleciona), não como "criar um trecho" minúsculo sem querer.
const MIN_REMOVED_SECONDS = 0.15;

// Hachurado do "removido pelo usuário" (spec §7: danger hachurado). Inline
// porque o Tailwind não tem utilitário de gradiente repetido; usa o token do
// tema via var() pra continuar seguindo a paleta.
const HATCH_STYLE = {
  backgroundImage:
    "repeating-linear-gradient(45deg, var(--color-danger-text-strong) 0 3px, transparent 3px 7px)",
};

function formatTimecode(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  const rest = s - m * 60;
  return `${m.toString().padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
}

function overlaps(a: TimeRange, range: TimeRange): boolean {
  return a.end > range.start && a.start < range.end;
}

// Mantém a lista de removidos ordenada e sem sobreposição — o backend
// também mescla, mas a trilha fica mais legível (e o Delete mais
// previsível) se a tela não mostrar dois blocos um em cima do outro.
function sortAndMerge(ranges: TimeRange[]): TimeRange[] {
  const sorted = ranges.filter((r) => r.end > r.start).map((r) => ({ ...r })).sort((a, b) => a.start - b.start);
  const merged: TimeRange[] = [];
  for (const r of sorted) {
    const last = merged[merged.length - 1];
    if (last && r.start <= last.end) last.end = Math.max(last.end, r.end);
    else merged.push(r);
  }
  return merged;
}

function tickStep(windowDuration: number): number {
  if (windowDuration <= 40) return 5;
  if (windowDuration <= 120) return 10;
  return 30;
}

interface Props {
  windowStart: number;
  windowEnd: number;
  range: TimeRange;
  captions: ClipDraft["captions"];
  pan: PanPointAbs[];
  removed: TimeRange[];
  profanityRanges: ProfanityRange[];
  hasStreamerRegion: boolean;
  /** Palavras pro snap; `[]` = snap desligado (fallback da spec §8). */
  words: readonly SnapWord[];
  playhead: number;
  /** Durante a renderização a timeline fica só leitura (spec §7). */
  readOnly: boolean;
  selectedCaptionIndex: number | null;
  onRangeChange: (range: TimeRange) => void;
  onRemovedChange: (removed: TimeRange[]) => void;
  onCaptionClick: (index: number) => void;
  onSeek: (time: number) => void;
}

/**
 * Timeline da aba "Revisão (beta)" — 4 trilhas (spec §7): Corte, Legendas,
 * Zoom e Removido/Mudo, mais a agulha do player. Só mexe no rascunho LOCAL
 * via callbacks: nenhuma chamada de API sai daqui (métrica #4 da spec).
 * O esmaecimento do que fica fora do corte é geometria simples, só visual;
 * a regra oficial de perdas é do backend (409 losses_require_confirmation).
 */
export function ReviewTimeline({
  windowStart,
  windowEnd,
  range,
  captions,
  pan,
  removed,
  profanityRanges,
  hasStreamerRegion,
  words,
  playhead,
  readOnly,
  selectedCaptionIndex,
  onRangeChange,
  onRemovedChange,
  onCaptionClick,
  onSeek,
}: Props) {
  const tracksRef = useRef<HTMLDivElement>(null);
  const handleDragRef = useRef<SnapEdge | null>(null);
  const removedDragRef = useRef<{ anchor: number; current: number } | null>(null);
  const [snappedEdge, setSnappedEdge] = useState<SnapEdge | null>(null);
  const [draftRemoved, setDraftRemoved] = useState<TimeRange | null>(null);
  const [selectedRemoved, setSelectedRemoved] = useState<number | null>(null);

  const windowDuration = Math.max(0.001, windowEnd - windowStart);
  const toPercent = (t: number) => ((t - windowStart) / windowDuration) * 100;
  const clampWindow = (t: number) => Math.min(windowEnd, Math.max(windowStart, t));
  const toTime = (clientX: number) => {
    const el = tracksRef.current;
    if (!el) return windowStart;
    const rect = el.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return windowStart + ratio * windowDuration;
  };

  // --- Corte: alças com snap -------------------------------------------------

  function moveEdge(edge: SnapEdge, rawTime: number, altKey: boolean) {
    const t = clampWindow(rawTime);
    const snap = snapTime(t, edge, words, { altKey });
    // Se o encaixe violar a duração mínima, fica com o valor solto (que é
    // re-clampado logo abaixo) — o snap nunca pode deixar o corte inválido.
    if (edge === "start") {
      const max = range.end - MIN_RANGE_SECONDS;
      const ok = snap.snapped && snap.time <= max && snap.time >= windowStart;
      onRangeChange({ start: Math.min(max, ok ? snap.time : t), end: range.end });
      setSnappedEdge(ok ? "start" : null);
      onSeek(Math.min(max, ok ? snap.time : t));
    } else {
      const min = range.start + MIN_RANGE_SECONDS;
      const ok = snap.snapped && snap.time >= min && snap.time <= windowEnd;
      onRangeChange({ start: range.start, end: Math.max(min, ok ? snap.time : t) });
      setSnappedEdge(ok ? "end" : null);
      onSeek(Math.max(min, ok ? snap.time : t));
    }
  }

  function handlePointerDown(edge: SnapEdge) {
    return (e: ReactPointerEvent<HTMLDivElement>) => {
      if (readOnly) return;
      e.preventDefault();
      e.stopPropagation();
      handleDragRef.current = edge;
      e.currentTarget.setPointerCapture(e.pointerId);
    };
  }

  function handlePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const edge = handleDragRef.current;
    if (!edge || readOnly) return;
    // `altKey` vem do próprio evento de ponteiro: reflete a tecla no
    // instante do movimento, sem precisar de listener de teclado à parte.
    moveEdge(edge, toTime(e.clientX), e.altKey);
  }

  function handlePointerUp() {
    handleDragRef.current = null;
    setSnappedEdge(null);
  }

  // Setas movem a alça em 0,1s (Shift: 1s) — sem snap, é ajuste fino.
  function handleEdgeKey(edge: SnapEdge) {
    return (e: ReactKeyboardEvent<HTMLDivElement>) => {
      if (readOnly || (e.key !== "ArrowLeft" && e.key !== "ArrowRight")) return;
      e.preventDefault();
      const delta = (e.key === "ArrowLeft" ? -1 : 1) * (e.shiftKey ? 1 : 0.1);
      const current = edge === "start" ? range.start : range.end;
      moveEdge(edge, current + delta, true);
    };
  }

  // --- Removido/Mudo: arrastar cria, clicar seleciona, Delete apaga ----------

  function handleRemovedPointerDown(e: ReactPointerEvent<HTMLDivElement>) {
    if (readOnly) return;
    e.currentTarget.focus();
    e.currentTarget.setPointerCapture(e.pointerId);
    const t = toTime(e.clientX);
    removedDragRef.current = { anchor: t, current: t };
  }

  function handleRemovedPointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const drag = removedDragRef.current;
    if (!drag || readOnly) return;
    drag.current = toTime(e.clientX);
    setDraftRemoved({ start: Math.min(drag.anchor, drag.current), end: Math.max(drag.anchor, drag.current) });
  }

  function handleRemovedPointerUp(e: ReactPointerEvent<HTMLDivElement>) {
    const drag = removedDragRef.current;
    removedDragRef.current = null;
    setDraftRemoved(null);
    if (!drag || readOnly) return;
    const start = Math.min(drag.anchor, drag.current);
    const end = Math.max(drag.anchor, drag.current);
    if (end - start < MIN_REMOVED_SECONDS) {
      // Foi um clique: seleciona o trecho do usuário sob o ponteiro (se
      // houver) ou só limpa a seleção e leva o player até ali.
      const hit = removed.findIndex((r) => drag.anchor >= r.start && drag.anchor <= r.end);
      setSelectedRemoved(hit >= 0 ? hit : null);
      if (hit < 0) onSeek(toTime(e.clientX));
      return;
    }
    const next = sortAndMerge([...removed, { start, end }]);
    onRemovedChange(next);
    setSelectedRemoved(next.findIndex((r) => r.start <= start && r.end >= end));
  }

  function handleRemovedKeyDown(e: ReactKeyboardEvent<HTMLDivElement>) {
    if (readOnly || selectedRemoved === null) return;
    if (e.key === "Delete" || e.key === "Backspace") {
      e.preventDefault();
      onRemovedChange(removed.filter((_, i) => i !== selectedRemoved));
      setSelectedRemoved(null);
    }
  }

  // --- Desenho ---------------------------------------------------------------

  const startPct = toPercent(range.start);
  const endPct = toPercent(range.end);
  const step = tickStep(windowDuration);
  const ticks: number[] = [];
  for (let t = Math.ceil(windowStart / step) * step; t <= windowEnd; t += step) ticks.push(t);
  const playheadVisible = playhead >= windowStart && playhead <= windowEnd;
  const fadedClass = "opacity-30";

  const rowLabel = "w-24 shrink-0 text-[11px] font-medium text-text-muted sm:w-28";
  const trackBase = "relative h-8 w-full rounded-md bg-surface-hover";

  return (
    <div className="flex flex-col gap-1.5 select-none">
      {/* Régua de tempo */}
      <div className="flex items-end gap-2">
        <span className={rowLabel} />
        <div className="relative h-4 w-full text-[10px] text-text-subtle">
          {ticks.map((t) => (
            <span key={t} className="absolute -translate-x-1/2" style={{ left: `${toPercent(t)}%` }}>
              {formatTimecode(t).slice(0, 5)}
            </span>
          ))}
        </div>
      </div>

      <div className="flex gap-2">
        <div className="flex flex-col gap-1.5">
          <span className={cn(rowLabel, "flex h-8 items-center")}>Corte</span>
          <span className={cn(rowLabel, "flex h-8 items-center")}>Legendas</span>
          <span className={cn(rowLabel, "flex h-8 items-center")}>Zoom</span>
          <span className={cn(rowLabel, "flex h-8 items-center")}>Removido/Mudo</span>
        </div>

        <div ref={tracksRef} className="relative flex w-full flex-col gap-1.5">
          {/* Trilha Corte (token accent) */}
          <div
            className={cn(trackBase, "touch-none")}
            onPointerMove={handlePointerMove}
            onPointerUp={handlePointerUp}
            onPointerCancel={handlePointerUp}
            onClick={(e) => {
              if (!handleDragRef.current) onSeek(toTime(e.clientX));
            }}
          >
            <div className="absolute inset-0 rounded-md bg-surface/60" />
            <div
              className="absolute top-0 h-full bg-accent/60"
              style={{ left: `${startPct}%`, width: `${Math.max(0, endPct - startPct)}%` }}
            />
            {(["start", "end"] as const).map((edge) => (
              <div
                key={edge}
                onPointerDown={handlePointerDown(edge)}
                onKeyDown={handleEdgeKey(edge)}
                onClick={(e) => e.stopPropagation()}
                tabIndex={readOnly ? -1 : 0}
                role="slider"
                aria-label={edge === "start" ? "Início do corte" : "Fim do corte"}
                aria-valuemin={edge === "start" ? windowStart : range.start + MIN_RANGE_SECONDS}
                aria-valuemax={edge === "start" ? range.end - MIN_RANGE_SECONDS : windowEnd}
                aria-valuenow={edge === "start" ? range.start : range.end}
                aria-valuetext={formatTimecode(edge === "start" ? range.start : range.end)}
                aria-disabled={readOnly}
                className={cn(
                  "absolute top-0 h-full w-3 -translate-x-1/2 rounded-sm bg-accent-text-hover shadow",
                  readOnly ? "cursor-not-allowed opacity-60" : "cursor-ew-resize hover:bg-accent-subtle-text",
                  snappedEdge === edge && "outline-2 outline-offset-1 outline-accent-subtle-text [--tw-outline-style:solid]",
                  MOTION_SAFE_TRANSITION,
                  FOCUS_VISIBLE_RING,
                )}
                style={{ left: `${edge === "start" ? startPct : endPct}%` }}
              />
            ))}
          </div>

          {/* Trilha Legendas (azul): sólida = manual, tracejada = auto */}
          <div className={trackBase}>
            {captions.lines.map((line, i) => {
              const outside = !overlaps(line, range);
              return (
                <button
                  key={`${line.start}-${i}`}
                  type="button"
                  disabled={readOnly}
                  onClick={() => onCaptionClick(i)}
                  title={`${formatTimecode(line.start)} → ${formatTimecode(line.end)}: ${line.text}`}
                  className={cn(
                    "absolute top-1 bottom-1 overflow-hidden rounded-sm border-2 bg-blue-500/30 px-1 text-left text-[10px] leading-5 whitespace-nowrap text-text",
                    captions.mode === "manual" ? "border-solid border-blue-400" : "border-dashed border-blue-400/80",
                    selectedCaptionIndex === i && "bg-blue-500/60 ring-1 ring-blue-200",
                    outside && fadedClass,
                    !readOnly && "hover:bg-blue-500/50",
                    MOTION_SAFE_TRANSITION,
                    FOCUS_VISIBLE_RING,
                  )}
                  style={{
                    left: `${toPercent(line.start)}%`,
                    width: `${Math.max(0.5, toPercent(line.end) - toPercent(line.start))}%`,
                  }}
                >
                  {line.text}
                </button>
              );
            })}
          </div>

          {/* Trilha Zoom (warning-marker): losangos, só leitura */}
          <div className={trackBase}>
            {hasStreamerRegion ? (
              <p className="flex h-full items-center px-2 text-[11px] text-text-subtle">
                Zoom manual indisponível: este job usa tela dividida (streamer).
              </p>
            ) : pan.length === 0 ? (
              <p className="flex h-full items-center px-2 text-[11px] text-text-subtle">
                Enquadramento automático — clique no vídeo original pra marcar um ponto.
              </p>
            ) : (
              pan.map((p) => (
                <button
                  key={p.t}
                  type="button"
                  onClick={() => onSeek(p.t)}
                  title={`${formatTimecode(p.t)} → foco em ${(p.cx * 100).toFixed(0)}%`}
                  aria-label={`Ponto de zoom em ${formatTimecode(p.t)}`}
                  className={cn(
                    "absolute top-1/2 h-3 w-3 -translate-x-1/2 -translate-y-1/2 rotate-45 bg-warning-marker",
                    (p.t < range.start || p.t > range.end) && fadedClass,
                    FOCUS_VISIBLE_RING,
                  )}
                  style={{ left: `${toPercent(p.t)}%` }}
                />
              ))
            )}
          </div>

          {/* Trilha Removido/Mudo: hachurado = usuário, cinza+cadeado = filtro */}
          <div
            tabIndex={readOnly ? -1 : 0}
            aria-label="Trechos removidos — arraste pra criar, clique pra selecionar, Delete pra apagar"
            className={cn(trackBase, "touch-none", readOnly ? "cursor-not-allowed" : "cursor-crosshair", FOCUS_VISIBLE_RING)}
            onPointerDown={handleRemovedPointerDown}
            onPointerMove={handleRemovedPointerMove}
            onPointerUp={handleRemovedPointerUp}
            onPointerCancel={() => {
              removedDragRef.current = null;
              setDraftRemoved(null);
            }}
            onKeyDown={handleRemovedKeyDown}
          >
            {profanityRanges.map((r, i) => (
              <div
                key={`p-${r.start}-${i}`}
                title={`Filtro de palavrões (${r.kind === "muted" ? "mudo" : "cortado"}) — só leitura`}
                className={cn(
                  "pointer-events-none absolute top-1 bottom-1 flex items-center justify-center overflow-hidden rounded-sm bg-surface-active-strong text-[9px]",
                  !overlaps(r, range) && fadedClass,
                )}
                style={{
                  left: `${toPercent(r.start)}%`,
                  width: `${Math.max(0.5, toPercent(r.end) - toPercent(r.start))}%`,
                }}
              >
                <span aria-hidden="true">🔒</span>
              </div>
            ))}
            {removed.map((r, i) => (
              <div
                key={`r-${r.start}-${i}`}
                title={`Removido: ${formatTimecode(r.start)} → ${formatTimecode(r.end)}`}
                className={cn(
                  "pointer-events-none absolute top-1 bottom-1 rounded-sm border border-danger-border bg-danger-bg/60",
                  selectedRemoved === i && "border-2 border-danger-text",
                  !overlaps(r, range) && fadedClass,
                )}
                style={{
                  ...HATCH_STYLE,
                  left: `${toPercent(r.start)}%`,
                  width: `${Math.max(0.5, toPercent(r.end) - toPercent(r.start))}%`,
                }}
              />
            ))}
            {draftRemoved && (
              <div
                className="pointer-events-none absolute top-1 bottom-1 rounded-sm border border-dashed border-danger-text"
                style={{
                  ...HATCH_STYLE,
                  left: `${toPercent(draftRemoved.start)}%`,
                  width: `${toPercent(draftRemoved.end) - toPercent(draftRemoved.start)}%`,
                }}
              />
            )}
          </div>

          {/* Agulha (posição do player) sobre as 4 trilhas */}
          {playheadVisible && (
            <div
              className="pointer-events-none absolute top-0 bottom-0 w-0.5 -translate-x-1/2 bg-text"
              style={{ left: `${toPercent(playhead)}%` }}
              aria-hidden="true"
            />
          )}
        </div>
      </div>

      <div className="flex flex-wrap justify-between gap-2 pl-26 text-[10px] text-text-subtle sm:pl-30">
        <span>
          Arraste as alças do corte{words.length > 0 ? " (encaixa na palavra; segure Alt pra soltar livre)" : ""}.
          Na última trilha, arraste pra remover um trecho; clique pra selecionar e Delete pra apagar.
        </span>
        {selectedRemoved !== null && !readOnly && (
          <button
            type="button"
            className={cn("text-danger-text-strong hover:underline", FOCUS_VISIBLE_RING)}
            onClick={() => {
              onRemovedChange(removed.filter((_, i) => i !== selectedRemoved));
              setSelectedRemoved(null);
            }}
          >
            Apagar trecho selecionado
          </button>
        )}
      </div>
    </div>
  );
}
