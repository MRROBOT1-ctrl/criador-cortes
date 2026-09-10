import { useState } from "react";
import { clipFileUrl, metricsUiUrl, recutClip } from "../api";
import type { CaptionFontFamily, CaptionStyle, ClipOut, Platform } from "../types";
import { CaptionSection } from "./CaptionSection";

const PLATFORM_LABELS: Record<Platform, string> = {
  tiktok: "TikTok",
  instagram_reels: "Instagram Reels",
  youtube_shorts: "YouTube Shorts",
  facebook_reels: "Facebook Reels",
};

interface Props {
  clip: ClipOut;
  onUpdated: (clip: ClipOut) => void;
}

export function ClipCard({ clip, onUpdated }: Props) {
  const [copiedPlatform, setCopiedPlatform] = useState<string | null>(null);
  const [recutStart, setRecutStart] = useState(clip.start_time.toFixed(2));
  const [recutEnd, setRecutEnd] = useState(clip.end_time.toFixed(2));
  const [recutting, setRecutting] = useState(false);
  const [recutError, setRecutError] = useState<string | null>(null);
  const [showEditor, setShowEditor] = useState(false);
  // Sem valor "atual" pra refletir (ClipOut não expõe o estilo aplicado,
  // só o Job tem isso) — parte de um default razoável que o usuário ajusta.
  const [captionsEnabled, setCaptionsEnabled] = useState(true);
  const [captionFontSize, setCaptionFontSize] = useState(8);
  const [captionColor, setCaptionColor] = useState("#FFFFFF");
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>("bold_outline");
  const [captionFontFamily, setCaptionFontFamily] = useState<CaptionFontFamily>("DejaVu Sans");
  // Muda a cada recorte bem-sucedido para forçar o <video> a recarregar o
  // arquivo (mesma URL, conteúdo novo — o navegador cacheia por padrão).
  const [videoVersion, setVideoVersion] = useState(0);

  async function copyHashtags(platform: string, tags: string[]) {
    try {
      await navigator.clipboard.writeText(tags.join(" "));
      setCopiedPlatform(platform);
      setTimeout(() => setCopiedPlatform((current) => (current === platform ? null : current)), 1500);
    } catch {
      // clipboard indisponível (ex: contexto não seguro) — ignora silenciosamente
    }
  }

  async function handleRecut() {
    setRecutError(null);
    const start = Number(recutStart);
    const end = Number(recutEnd);
    if (Number.isNaN(start) || Number.isNaN(end) || end <= start) {
      setRecutError("Informe início e fim válidos (fim maior que início).");
      return;
    }
    setRecutting(true);
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
      setVideoVersion((v) => v + 1);
    } catch (err) {
      setRecutError(err instanceof Error ? err.message : "Falha ao editar o clipe.");
    } finally {
      setRecutting(false);
    }
  }

  const hashtagEntries = Object.entries(clip.hashtags ?? {}) as [Platform, string[]][];
  const videoSrc = `${clipFileUrl(clip.id)}?v=${videoVersion}`;

  return (
    <div className="flex flex-col gap-4 rounded-xl border border-slate-800 bg-slate-900 p-4 shadow-lg">
      <video
        key={videoVersion}
        src={videoSrc}
        controls
        className="mx-auto aspect-[9/16] w-full max-w-56 rounded-lg bg-black"
      />

      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-slate-300">
        <span>
          Corte #{clip.index + 1}: {clip.start_time.toFixed(1)}s → {clip.end_time.toFixed(1)}s
        </span>
        <span className="rounded-full bg-slate-800 px-2 py-0.5 text-xs font-semibold text-slate-300">
          score {clip.score.toFixed(0)}
        </span>
      </div>

      {clip.transcript_text && (
        <p className="max-h-24 overflow-auto rounded-lg bg-slate-950 p-2 text-xs text-slate-400">
          {clip.transcript_text}
        </p>
      )}

      {hashtagEntries.length > 0 && (
        <div className="flex flex-col gap-2">
          {hashtagEntries.map(([platform, tags]) => (
            <div key={platform} className="flex flex-col gap-1 rounded-lg bg-slate-950 p-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-300">
                  {PLATFORM_LABELS[platform] ?? platform}
                </span>
                <button
                  type="button"
                  onClick={() => copyHashtags(platform, tags)}
                  className="text-xs font-medium text-indigo-400 hover:text-indigo-300"
                >
                  {copiedPlatform === platform ? "Copiado!" : "Copiar"}
                </button>
              </div>
              <p className="text-xs text-slate-500">{tags.join(" ")}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-col gap-2 rounded-lg border border-slate-800 p-2.5">
        <button
          type="button"
          onClick={() => setShowEditor((v) => !v)}
          className="text-left text-xs font-semibold text-slate-300 hover:text-slate-100"
        >
          {showEditor ? "▾" : "▸"} Editar clipe (tempo, legenda, fonte...)
        </button>

        {showEditor && (
          <>
            <div className="flex items-center gap-2">
              <input
                type="number"
                step="0.1"
                value={recutStart}
                onChange={(e) => setRecutStart(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-100"
                aria-label="Início (s)"
              />
              <span className="text-xs text-slate-500">até</span>
              <input
                type="number"
                step="0.1"
                value={recutEnd}
                onChange={(e) => setRecutEnd(e.target.value)}
                className="w-full rounded-md border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-100"
                aria-label="Fim (s)"
              />
            </div>

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

            {recutError && <p className="text-xs text-red-400">{recutError}</p>}
            <button
              type="button"
              onClick={handleRecut}
              disabled={recutting}
              className="rounded-md bg-slate-700 px-3 py-1.5 text-xs font-semibold text-slate-100 hover:bg-slate-600 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {recutting ? "Aplicando..." : "Aplicar"}
            </button>
          </>
        )}
      </div>

      <div className="flex flex-wrap gap-2">
        <a
          href={clipFileUrl(clip.id)}
          download
          className="flex-1 rounded-lg bg-indigo-600 px-3 py-2 text-center text-xs font-semibold text-white hover:bg-indigo-500"
        >
          Baixar clipe
        </a>
        <a
          href={metricsUiUrl(clip.id)}
          target="_blank"
          rel="noreferrer"
          className="flex-1 rounded-lg bg-slate-800 px-3 py-2 text-center text-xs font-semibold text-slate-200 hover:bg-slate-700"
        >
          Ver métricas
        </a>
      </div>
    </div>
  );
}
