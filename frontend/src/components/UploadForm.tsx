import { useState } from "react";
import type { FormEvent } from "react";
import { uploadVideo, uploadVideoUrl } from "../api";
import type { CaptionFontFamily, CaptionStyle, UploadConfig, UploadResponse, WatermarkPosition } from "../types";
import { CaptionSection } from "./CaptionSection";
import { RelaxingVideoSection } from "./RelaxingVideoSection";
import { WatermarkSection } from "./WatermarkSection";

const ACCEPTED_EXTENSIONS = [".mp4", ".mov", ".avi", ".mkv"];

type Source = "file" | "url";

interface Props {
  onUploaded: (response: UploadResponse) => void;
}

export function UploadForm({ onUploaded }: Props) {
  const [source, setSource] = useState<Source>("file");
  const [file, setFile] = useState<File | null>(null);
  const [url, setUrl] = useState("");
  const [maxClips, setMaxClips] = useState(3);
  const [durationMin, setDurationMin] = useState(15);
  const [durationMax, setDurationMax] = useState(60);
  const [keywords, setKeywords] = useState("");
  const [language, setLanguage] = useState("");
  const [watermarkEnabled, setWatermarkEnabled] = useState(false);
  const [watermarkPosition, setWatermarkPosition] = useState<WatermarkPosition>("bottom-right");
  const [captionsEnabled, setCaptionsEnabled] = useState(true);
  const [captionFontSize, setCaptionFontSize] = useState(8);
  const [captionColor, setCaptionColor] = useState("#FFFFFF");
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>("bold_outline");
  const [captionFontFamily, setCaptionFontFamily] = useState<CaptionFontFamily>("DejaVu Sans");
  const [useRelaxingVideoFallback, setUseRelaxingVideoFallback] = useState(true);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);

    if (durationMax <= durationMin) {
      setError("A duração máxima deve ser maior que a duração mínima do clipe.");
      return;
    }

    const config: UploadConfig = {
      max_clips: maxClips,
      clip_duration_min: durationMin,
      clip_duration_max: durationMax,
      keywords,
      language,
      watermark_enabled: watermarkEnabled,
      watermark_position: watermarkPosition,
      captions_enabled: captionsEnabled,
      caption_font_size: captionFontSize,
      caption_color: captionColor,
      caption_style: captionStyle,
      caption_font_family: captionFontFamily,
      use_relaxing_video_fallback: useRelaxingVideoFallback,
    };

    setSubmitting(true);
    try {
      let response: UploadResponse;
      if (source === "file") {
        if (!file) {
          setError("Selecione um arquivo de vídeo primeiro.");
          return;
        }
        const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
        if (!ACCEPTED_EXTENSIONS.includes(ext)) {
          setError(`Formato "${ext}" não suportado. Use: ${ACCEPTED_EXTENSIONS.join(", ")}.`);
          return;
        }
        response = await uploadVideo(file, config);
      } else {
        const trimmed = url.trim();
        if (!trimmed.toLowerCase().startsWith("http://") && !trimmed.toLowerCase().startsWith("https://")) {
          setError("Cole um link válido (começando com http:// ou https://).");
          return;
        }
        response = await uploadVideoUrl(trimmed, config);
      }
      onUploaded(response);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao enviar o vídeo.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="mx-auto flex w-full max-w-xl flex-col gap-5 rounded-xl border border-slate-800 bg-slate-900 p-6 shadow-lg"
    >
      <div>
        <h2 className="text-lg font-semibold text-slate-100">Enviar vídeo</h2>
        <p className="mt-1 text-sm text-slate-400">
          Envie um vídeo longo (podcast, gameplay, etc.) para gerar cortes verticais automáticos.
        </p>
      </div>

      <div className="flex gap-1 rounded-lg border border-slate-700 bg-slate-800 p-1">
        {(["file", "url"] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setSource(option)}
            className={`flex-1 rounded-md px-3 py-1.5 text-sm font-medium transition ${
              source === option
                ? "bg-indigo-600 text-white"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            {option === "file" ? "Enviar arquivo" : "Colar link"}
          </button>
        ))}
      </div>

      {source === "file" ? (
        <div className="flex flex-col gap-1.5">
          <label htmlFor="file" className="text-sm font-medium text-slate-300">
            Arquivo de vídeo (mp4, mov, avi, mkv)
          </label>
          <input
            id="file"
            type="file"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
            className="block w-full cursor-pointer rounded-lg border border-slate-700 bg-slate-800 text-sm text-slate-300 file:mr-4 file:cursor-pointer file:rounded-md file:border-0 file:bg-indigo-600 file:px-4 file:py-2 file:text-sm file:font-medium file:text-white hover:file:bg-indigo-500"
          />
          {file && (
            <span className="text-xs text-slate-500">
              {file.name} ({(file.size / (1024 * 1024)).toFixed(1)} MB)
            </span>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <label htmlFor="url" className="text-sm font-medium text-slate-300">
            Link do vídeo (YouTube, TikTok, Instagram etc.)
          </label>
          <input
            id="url"
            type="url"
            placeholder="https://www.youtube.com/watch?v=..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500"
          />
          <span className="text-xs text-slate-500">
            O vídeo é baixado pelo servidor (via yt-dlp) antes de processar — pode levar um
            pouco mais pra começar do que o upload direto de arquivo.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <div className="flex flex-col gap-1.5">
          <label htmlFor="max_clips" className="text-sm font-medium text-slate-300">
            Máx. de clipes
          </label>
          <input
            id="max_clips"
            type="number"
            min={1}
            max={20}
            value={maxClips}
            onChange={(e) => setMaxClips(Number(e.target.value))}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="language" className="text-sm font-medium text-slate-300">
            Idioma (opcional)
          </label>
          <input
            id="language"
            type="text"
            placeholder="ex: pt (autodetectar se vazio)"
            value={language}
            onChange={(e) => setLanguage(e.target.value)}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="duration_min" className="text-sm font-medium text-slate-300">
            Duração mín. do clipe (s)
          </label>
          <input
            id="duration_min"
            type="number"
            min={1}
            value={durationMin}
            onChange={(e) => setDurationMin(Number(e.target.value))}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label htmlFor="duration_max" className="text-sm font-medium text-slate-300">
            Duração máx. do clipe (s)
          </label>
          <input
            id="duration_max"
            type="number"
            min={1}
            value={durationMax}
            onChange={(e) => setDurationMax(Number(e.target.value))}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          />
        </div>
      </div>

      <div className="flex flex-col gap-1.5">
        <label htmlFor="keywords" className="text-sm font-medium text-slate-300">
          Palavras-chave (separadas por vírgula, opcional)
        </label>
        <input
          id="keywords"
          type="text"
          placeholder="ex: futebol, gol, reação"
          value={keywords}
          onChange={(e) => setKeywords(e.target.value)}
          className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500"
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

      <WatermarkSection
        enabled={watermarkEnabled}
        position={watermarkPosition}
        onEnabledChange={setWatermarkEnabled}
        onPositionChange={setWatermarkPosition}
      />

      <RelaxingVideoSection enabled={useRelaxingVideoFallback} onEnabledChange={setUseRelaxingVideoFallback} />

      {error && (
        <div className="rounded-lg border border-red-800 bg-red-950/50 px-3 py-2 text-sm text-red-300">
          {error}
        </div>
      )}

      <button
        type="submit"
        disabled={submitting}
        className="mt-1 rounded-lg bg-indigo-600 px-4 py-2.5 text-sm font-semibold text-white transition hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
      >
        {submitting ? "Enviando..." : "Gerar cortes"}
      </button>
    </form>
  );
}
