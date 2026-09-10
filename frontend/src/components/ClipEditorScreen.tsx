import { useEffect, useRef, useState } from "react";
import { clipFileUrl, originalVideoUrl, recutClip } from "../api";
import type { CaptionFontFamily, CaptionStyle, ClipOut } from "../types";
import { CaptionSection } from "./CaptionSection";
import { CaptionTextEditor } from "./CaptionTextEditor";
import { CutSegmentsEditor } from "./CutSegmentsEditor";
import { ManualPanEditor } from "./ManualPanEditor";
import { TimelineScrubber } from "./TimelineScrubber";

// Folga (em segundos) mostrada de cada lado do corte atual na timeline —
// dá espaço pra estender o clipe pra trás/frente sem precisar saber a
// duração do vídeo inteiro. O backend valida o limite real (duração do
// vídeo original) no Aplicar; se o usuário arrastar além disso, a mensagem
// de erro do 400 aparece normal.
const WINDOW_PADDING_SECONDS = 60;

const PREVIEW_WIDTH_PX = 220;

// Calibração empírica (não uma fórmula exata do libass): testado
// visualmente que um caption_font_size=8 renderiza como texto de ~2 linhas
// legível no canvas final 1080x1920 — esse fator escala isso pra prévia.
const PREVIEW_FONT_SCALE = 2.3;

function captionOverlayStyle(
  style: CaptionStyle,
  color: string,
  fontFamily: CaptionFontFamily,
  fontSize: number,
): React.CSSProperties {
  const base: React.CSSProperties = {
    position: "absolute",
    left: "6%",
    right: "6%",
    textAlign: "center",
    fontFamily,
    color,
    fontWeight: style === "minimal" ? 400 : 700,
    fontSize: `${fontSize * PREVIEW_FONT_SCALE}px`,
    lineHeight: 1.2,
    wordBreak: "break-word",
  };
  if (style === "boxed") {
    return { ...base, bottom: "6%", background: "rgba(0,0,0,0.85)", padding: "4px 6px", borderRadius: 2 };
  }
  if (style === "no_outline") {
    return { ...base, bottom: "20%", textShadow: "none" };
  }
  if (style === "minimal") {
    return { ...base, bottom: "20%", textShadow: "0 0 2px #000, 0 0 2px #000" };
  }
  return {
    ...base,
    bottom: "20%",
    textShadow:
      "-1.5px -1.5px 0 #000, 1.5px -1.5px 0 #000, -1.5px 1.5px 0 #000, 1.5px 1.5px 0 #000, 0 2px 4px rgba(0,0,0,0.6)",
  };
}

const TABS = [
  { id: "corte", label: "Corte & estilo" },
  { id: "pan", label: "Zoom manual" },
  { id: "legenda", label: "Legenda" },
  { id: "cortes", label: "Remover trechos" },
] as const;
type TabId = (typeof TABS)[number]["id"];

interface Props {
  clip: ClipOut;
  onBack: () => void;
  onUpdated: (clip: ClipOut) => void;
}

/**
 * Tela cheia de edição de um clipe — cada aba corresponde a UM endpoint do
 * backend (recut / pan / captions / cuts), então só a aba ativa busca/
 * renderiza suas próprias coisas por vez (antes, no modal, as 5 seções
 * ficavam todas montadas e buscando dado ao mesmo tempo numa área pequena).
 */
export function ClipEditorScreen({ clip, onBack, onUpdated }: Props) {
  const [tab, setTab] = useState<TabId>("corte");

  const [start, setStart] = useState(clip.start_time);
  const [end, setEnd] = useState(clip.end_time);
  const [captionsEnabled, setCaptionsEnabled] = useState(true);
  const [captionFontSize, setCaptionFontSize] = useState(8);
  const [captionColor, setCaptionColor] = useState("#FFFFFF");
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>("bold_outline");
  const [captionFontFamily, setCaptionFontFamily] = useState<CaptionFontFamily>("DejaVu Sans");
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourcePreviewFailed, setSourcePreviewFailed] = useState(false);

  const sourceVideoRef = useRef<HTMLVideoElement>(null);

  // Se o usuário reabrir a edição de um clipe diferente sem desmontar o
  // componente (ex: navegação futura entre clipes), os campos de tempo/
  // estilo precisam refletir o NOVO clipe, não ficar presos no anterior.
  useEffect(() => {
    setStart(clip.start_time);
    setEnd(clip.end_time);
    setTab("corte");
  }, [clip.id]);

  const windowStart = Math.max(0, clip.start_time - WINDOW_PADDING_SECONDS);
  const windowEnd =
    clip.video_duration != null
      ? Math.min(clip.video_duration, clip.end_time + WINDOW_PADDING_SECONDS)
      : clip.end_time + WINDOW_PADDING_SECONDS;

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") onBack();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [onBack]);

  function seekSourceTo(time: number) {
    const video = sourceVideoRef.current;
    if (video && !sourcePreviewFailed) {
      video.currentTime = time;
    }
  }

  async function handleApply() {
    setError(null);
    if (end <= start) {
      setError("O fim precisa ser maior que o início.");
      return;
    }
    setApplying(true);
    try {
      const updated = await recutClip(clip.id, {
        start_time: start,
        end_time: end,
        captions_enabled: captionsEnabled,
        caption_font_size: captionFontSize,
        caption_color: captionColor,
        caption_style: captionStyle,
        caption_font_family: captionFontFamily,
      });
      onUpdated(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao aplicar as mudanças.");
    } finally {
      setApplying(false);
    }
  }

  const previewText = (clip.transcript_text ?? "").slice(0, 90) || "Prévia da legenda";

  return (
    <div className="mx-auto flex w-full max-w-4xl flex-col gap-5">
      <div className="flex items-center justify-between">
        <button
          type="button"
          onClick={onBack}
          className="text-sm font-medium text-indigo-400 hover:text-indigo-300"
        >
          ← Voltar pros cortes
        </button>
        <h2 className="text-lg font-semibold text-slate-100">Editando corte #{clip.index + 1}</h2>
      </div>

      <div className="flex gap-1 overflow-x-auto rounded-lg border border-slate-700 bg-slate-800 p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => setTab(t.id)}
            className={`flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium transition ${
              tab === t.id ? "bg-indigo-600 text-white" : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {/* Prévia do vídeo ORIGINAL — usada pela aba "Corte" (scrub) E pela aba
          "Zoom manual" (clicar pra marcar foco), então fica SEMPRE montada
          (nunca desmonta ao trocar de aba, só escondida via `hidden`) —
          senão `sourceVideoRef` ficaria nulo assim que o usuário saísse da
          aba "Corte", quebrando o zoom manual. */}
      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 shadow-lg" hidden={tab !== "corte" && tab !== "pan"}>
        <div className="mx-auto w-full max-w-[280px]">
          {!sourcePreviewFailed ? (
            <video
              ref={sourceVideoRef}
              src={originalVideoUrl(clip.job_id)}
              controls
              className="w-full rounded-lg bg-black"
              onLoadedMetadata={() => seekSourceTo(start)}
              onError={() => setSourcePreviewFailed(true)}
            />
          ) : (
            <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-slate-950 p-2 text-center text-xs text-slate-500">
              Prévia do vídeo original indisponível pra esse formato — os campos abaixo continuam
              funcionando normalmente.
            </div>
          )}
        </div>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900 p-5 shadow-lg">
        {tab === "corte" && (
          <div className="flex flex-col gap-5">
            <section className="flex flex-col gap-3">
              <p className="text-sm font-medium text-slate-300">Ajustar o corte</p>
              <TimelineScrubber
                windowStart={windowStart}
                windowEnd={windowEnd}
                start={start}
                end={end}
                onChange={(s, e) => {
                  setStart(s);
                  setEnd(e);
                }}
                onScrub={seekSourceTo}
              />
              <div className="flex items-center gap-2">
                <input
                  type="number"
                  step="0.1"
                  value={start.toFixed(2)}
                  onChange={(e) => setStart(Number(e.target.value))}
                  className="w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-100"
                  aria-label="Início (s)"
                />
                <span className="text-xs text-slate-500">até</span>
                <input
                  type="number"
                  step="0.1"
                  value={end.toFixed(2)}
                  onChange={(e) => setEnd(Number(e.target.value))}
                  className="w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-100"
                  aria-label="Fim (s)"
                />
              </div>
            </section>

            <section className="flex flex-col gap-3 border-t border-slate-800 pt-5 sm:flex-row">
              <div
                className="relative mx-auto aspect-[9/16] overflow-hidden rounded-lg bg-black"
                style={{ width: PREVIEW_WIDTH_PX }}
              >
                <video src={clipFileUrl(clip.id)} muted loop autoPlay className="h-full w-full object-cover" />
                {captionsEnabled && (
                  <div style={captionOverlayStyle(captionStyle, captionColor, captionFontFamily, captionFontSize)}>
                    {previewText}
                  </div>
                )}
              </div>
              <div className="flex-1">
                <p className="mb-2 text-xs text-slate-500">
                  Prévia aproximada (o resultado final é renderizado no servidor e pode variar um pouco).
                </p>
                <CaptionSection
                  captionsEnabled={captionsEnabled}
                  fontSize={captionFontSize}
                  color={captionColor}
                  style={captionStyle}
                  fontFamily={captionFontFamily}
                  onCaptionsEnabledChange={setCaptionsEnabled}
                  onFontSizeChange={setCaptionFontSize}
                  onColorChange={setCaptionColor}
                  onStyleChange={setCaptionStyle}
                  onFontFamilyChange={setCaptionFontFamily}
                />
              </div>
            </section>

            {error && (
              <div className="rounded-lg border border-red-800 bg-red-950/50 px-3 py-2 text-sm text-red-300">
                {error}
              </div>
            )}

            <div className="flex justify-end">
              <button
                type="button"
                onClick={handleApply}
                disabled={applying}
                className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
              >
                {applying ? "Aplicando... (pode levar um tempinho)" : "Aplicar"}
              </button>
            </div>
          </div>
        )}

        {tab === "pan" && (
          <ManualPanEditor
            clip={clip}
            sourceVideoRef={sourceVideoRef}
            sourcePreviewFailed={sourcePreviewFailed}
            hasStreamerRegion={clip.has_streamer_region}
            onApplied={onUpdated}
          />
        )}

        {tab === "legenda" && <CaptionTextEditor clip={clip} onApplied={onUpdated} />}

        {tab === "cortes" && <CutSegmentsEditor clip={clip} onApplied={onUpdated} />}
      </div>
    </div>
  );
}
