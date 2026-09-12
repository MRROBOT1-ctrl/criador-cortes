import { useState } from "react";
import type { FormEvent } from "react";
import { uploadVideo, uploadVideoUrl } from "../api";
import type {
  CaptionFontFamily,
  CaptionStyle,
  ProfanityFilter,
  UploadConfig,
  UploadResponse,
  WatermarkPosition,
} from "../types";
import { CaptionSection } from "./CaptionSection";
import { RelaxingVideoSection } from "./RelaxingVideoSection";
import { WatermarkSection } from "./WatermarkSection";
import { Button, Card, Input, Select, cn, FOCUS_VISIBLE_RING, MOTION_SAFE_TRANSITION } from "./ui";

const PROFANITY_OPTIONS = [
  { value: "off", label: "Desligado" },
  { value: "censor", label: "Censurar (muta o áudio + *** na legenda)" },
  { value: "cut", label: "Cortar (remove o trecho do vídeo)" },
];

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
  const [profanityFilter, setProfanityFilter] = useState<ProfanityFilter>("off");
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
      profanity_filter: profanityFilter,
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
    <Card as="form" onSubmit={handleSubmit} className="mx-auto flex w-full max-w-xl flex-col gap-5">
      <div>
        <h2 className="text-xl font-semibold text-text">Enviar vídeo</h2>
        <p className="mt-1 text-sm text-text-muted">
          Envie um vídeo longo (podcast, gameplay, etc.) para gerar cortes verticais automáticos.
        </p>
      </div>

      <div className="flex gap-1 rounded-lg border border-border-strong bg-surface-hover p-1">
        {(["file", "url"] as const).map((option) => (
          <button
            key={option}
            type="button"
            onClick={() => setSource(option)}
            className={cn(
              "flex-1 rounded-md px-3 py-1.5 text-sm font-medium",
              MOTION_SAFE_TRANSITION,
              FOCUS_VISIBLE_RING,
              "active:brightness-90 motion-safe:active:scale-[0.98]",
              source === option ? "bg-accent text-white" : "text-text-muted hover:text-heading",
            )}
          >
            {option === "file" ? "Enviar arquivo" : "Colar link"}
          </button>
        ))}
      </div>

      {source === "file" ? (
        <div className="flex flex-col gap-1.5">
          <Input
            variant="file"
            id="file"
            label="Arquivo de vídeo (mp4, mov, avi, mkv)"
            accept={ACCEPTED_EXTENSIONS.join(",")}
            onChange={(e) => setFile(e.target.files?.[0] ?? null)}
          />
          {file && (
            <span className="text-xs text-text-subtle">
              {file.name} ({(file.size / (1024 * 1024)).toFixed(1)} MB)
            </span>
          )}
        </div>
      ) : (
        <div className="flex flex-col gap-1.5">
          <Input
            variant="url"
            id="url"
            label="Link do vídeo (YouTube, TikTok, Instagram etc.)"
            placeholder="https://www.youtube.com/watch?v=..."
            value={url}
            onChange={(e) => setUrl(e.target.value)}
          />
          <span className="text-xs text-text-subtle">
            O vídeo é baixado pelo servidor (via yt-dlp) antes de processar — pode levar um
            pouco mais pra começar do que o upload direto de arquivo.
          </span>
        </div>
      )}

      <div className="grid grid-cols-2 gap-4">
        <Input
          variant="number"
          id="max_clips"
          label="Máx. de clipes"
          min={1}
          max={20}
          value={maxClips}
          onChange={(e) => setMaxClips(Number(e.target.value))}
        />

        <Input
          variant="text"
          id="language"
          label="Idioma (opcional)"
          // Texto mais curto que o original ("ex: pt (autodetectar se
          // vazio)") — Parte 4: nas 2 colunas de 375px (~138px cada) o
          // placeholder longo ficava cortado (mesmo a versão intermediária
          // "vazio = auto" ainda cortava a última letra — sugestão do
          // usuário: "pt / auto" cabe inteiro). O comportamento de
          // autodetectar não muda, só o texto de exemplo fica mais enxuto.
          placeholder="ex: pt / auto"
          value={language}
          onChange={(e) => setLanguage(e.target.value)}
        />

        <Input
          variant="number"
          id="duration_min"
          label="Duração mín. do clipe (s)"
          min={1}
          value={durationMin}
          onChange={(e) => setDurationMin(Number(e.target.value))}
        />

        <Input
          variant="number"
          id="duration_max"
          label="Duração máx. do clipe (s)"
          min={1}
          value={durationMax}
          onChange={(e) => setDurationMax(Number(e.target.value))}
        />
      </div>

      <Input
        variant="text"
        id="keywords"
        label="Palavras-chave (separadas por vírgula, opcional)"
        placeholder="ex: futebol, gol, reação"
        value={keywords}
        onChange={(e) => setKeywords(e.target.value)}
      />

      <div className="flex flex-col gap-1.5">
        {/* Esse select fica no nível "de cima" do formulário (ao lado de
            max_clips/language), por isso o label maior — o label PADRÃO do
            Select (menor, pros selects "de dentro" de seções tipo Legenda/
            Marca d'água) não bate aqui, então escrevo o label à parte. */}
        <label htmlFor="profanity_filter" className="text-sm font-medium text-text-secondary">
          Filtro de palavrões
        </label>
        <Select
          id="profanity_filter"
          options={PROFANITY_OPTIONS}
          value={profanityFilter}
          onChange={(e) => setProfanityFilter(e.target.value as ProfanityFilter)}
        />
        <span className="text-xs text-text-subtle">
          Best-effort por lista de palavras — não pega tudo, e depende da transcrição do
          Whisper ter acertado a palavra.
        </span>
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
        <div className="rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
          {error}
        </div>
      )}

      <Button type="submit" loading={submitting} className="mt-1">
        {submitting ? "Enviando..." : "Gerar cortes"}
      </Button>
    </Card>
  );
}
