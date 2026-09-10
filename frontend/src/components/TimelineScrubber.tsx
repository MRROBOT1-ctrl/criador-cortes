import { useRef } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";

interface Props {
  windowStart: number; // segundos — limite esquerdo da janela mostrada
  windowEnd: number; // segundos — limite direito da janela mostrada
  start: number;
  end: number;
  onChange: (start: number, end: number) => void;
  onScrub?: (time: number) => void; // chamado enquanto arrasta, com o tempo do handle ativo
  minDuration?: number; // duração mínima entre start/end, evita os handles colapsarem um no outro
}

function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const m = Math.floor(s / 60);
  const sec = s % 60;
  return `${m}:${sec.toString().padStart(2, "0")}`;
}

/**
 * Timeline com dois handles arrastáveis (início/fim) — implementado com
 * pointer events direto (não dois <input type="range"> sobrepostos) pra ter
 * controle total do clamp/colisão entre os handles. `windowStart`/`windowEnd`
 * definem a janela de tempo mostrada (não precisa ser o vídeo inteiro — ver
 * ClipEditorModal, que usa uma janela com folga ao redor do clipe atual).
 */
export function TimelineScrubber({ windowStart, windowEnd, start, end, onChange, onScrub, minDuration = 0.5 }: Props) {
  const trackRef = useRef<HTMLDivElement>(null);
  const draggingRef = useRef<"start" | "end" | null>(null);
  const windowDuration = Math.max(0.001, windowEnd - windowStart);

  const toPercent = (t: number) => ((t - windowStart) / windowDuration) * 100;
  const toTime = (clientX: number) => {
    const track = trackRef.current;
    if (!track) return windowStart;
    const rect = track.getBoundingClientRect();
    const ratio = Math.min(1, Math.max(0, (clientX - rect.left) / rect.width));
    return windowStart + ratio * windowDuration;
  };

  function handlePointerDown(which: "start" | "end") {
    return (e: ReactPointerEvent<HTMLDivElement>) => {
      e.preventDefault();
      draggingRef.current = which;
      e.currentTarget.setPointerCapture(e.pointerId);
    };
  }

  function handlePointerMove(e: ReactPointerEvent<HTMLDivElement>) {
    const which = draggingRef.current;
    if (!which) return;
    const t = toTime(e.clientX);
    if (which === "start") {
      const newStart = Math.max(windowStart, Math.min(t, end - minDuration));
      onChange(newStart, end);
      onScrub?.(newStart);
    } else {
      const newEnd = Math.min(windowEnd, Math.max(t, start + minDuration));
      onChange(start, newEnd);
      onScrub?.(newEnd);
    }
  }

  function handlePointerUp() {
    draggingRef.current = null;
  }

  const startPct = toPercent(start);
  const endPct = toPercent(end);

  return (
    <div className="flex flex-col gap-1">
      <div
        ref={trackRef}
        className="relative h-9 w-full touch-none select-none rounded-md bg-slate-800"
        onPointerMove={handlePointerMove}
        onPointerUp={handlePointerUp}
      >
        {/* trechos fora do intervalo selecionado, escurecidos */}
        <div className="absolute inset-0 rounded-md bg-slate-900/60" />
        {/* faixa selecionada */}
        <div
          className="absolute top-0 h-full bg-indigo-600/60"
          style={{ left: `${startPct}%`, width: `${Math.max(0, endPct - startPct)}%` }}
        />
        {/* handle início */}
        <div
          onPointerDown={handlePointerDown("start")}
          className="absolute top-0 h-full w-3 -translate-x-1/2 cursor-ew-resize rounded-sm bg-indigo-300 shadow hover:bg-indigo-200"
          style={{ left: `${startPct}%` }}
          role="slider"
          aria-label="Início do corte"
          aria-valuenow={start}
        />
        {/* handle fim */}
        <div
          onPointerDown={handlePointerDown("end")}
          className="absolute top-0 h-full w-3 -translate-x-1/2 cursor-ew-resize rounded-sm bg-indigo-300 shadow hover:bg-indigo-200"
          style={{ left: `${endPct}%` }}
          role="slider"
          aria-label="Fim do corte"
          aria-valuenow={end}
        />
      </div>
      <div className="flex justify-between text-[10px] text-slate-500">
        <span>{formatTime(windowStart)}</span>
        <span className="font-medium text-slate-300">
          {formatTime(start)} → {formatTime(end)} ({(end - start).toFixed(1)}s)
        </span>
        <span>{formatTime(windowEnd)}</span>
      </div>
    </div>
  );
}
