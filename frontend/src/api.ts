// Cliente HTTP simples com fetch nativo — sem axios/react-query, conforme spec.
import type {
  CaptionFontFamily,
  CaptionLine,
  CaptionLinesOut,
  CaptionStyle,
  ClipOut,
  JobOut,
  JobSummary,
  PanKeyframe,
  PanKeyframesOut,
  UploadConfig,
  UploadResponse,
} from "./types";

export interface WatermarkStatus {
  exists: boolean;
}

export const API_URL: string = import.meta.env.VITE_API_URL || "http://localhost:8000";

// ws:// ou wss:// a partir de API_URL, preservando host/porta.
export function wsUrl(path: string): string {
  const base = new URL(API_URL);
  const protocol = base.protocol === "https:" ? "wss:" : "ws:";
  return `${protocol}//${base.host}${path}`;
}

async function handleJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      // resposta sem corpo JSON (ex: erro de rede) — mantém statusText
    }
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return (await res.json()) as T;
}

export async function uploadVideo(file: File, config: UploadConfig): Promise<UploadResponse> {
  const form = new FormData();
  form.append("file", file);
  form.append("max_clips", String(config.max_clips));
  form.append("clip_duration_min", String(config.clip_duration_min));
  form.append("clip_duration_max", String(config.clip_duration_max));
  form.append("keywords", config.keywords);
  if (config.language.trim()) {
    form.append("language", config.language.trim());
  }
  form.append("watermark_enabled", String(config.watermark_enabled));
  form.append("watermark_position", config.watermark_position);
  form.append("captions_enabled", String(config.captions_enabled));
  form.append("caption_font_size", String(config.caption_font_size));
  form.append("caption_color", config.caption_color);
  form.append("caption_style", config.caption_style);
  form.append("caption_font_family", config.caption_font_family);

  const res = await fetch(`${API_URL}/upload`, { method: "POST", body: form });
  return handleJson<UploadResponse>(res);
}

// Alternativa ao upload de arquivo: link do YouTube/TikTok/Instagram etc.,
// baixado via yt-dlp dentro do worker (ver POST /upload-url no backend).
export async function uploadVideoUrl(url: string, config: UploadConfig): Promise<UploadResponse> {
  const res = await fetch(`${API_URL}/upload-url`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      url,
      max_clips: config.max_clips,
      clip_duration_min: config.clip_duration_min,
      clip_duration_max: config.clip_duration_max,
      keywords: config.keywords,
      language: config.language.trim() || null,
      watermark_enabled: config.watermark_enabled,
      watermark_position: config.watermark_position,
      captions_enabled: config.captions_enabled,
      caption_font_size: config.caption_font_size,
      caption_color: config.caption_color,
      caption_style: config.caption_style,
      caption_font_family: config.caption_font_family,
    }),
  });
  return handleJson<UploadResponse>(res);
}

export async function getJobStatus(jobId: string): Promise<JobOut> {
  const res = await fetch(`${API_URL}/status/${jobId}`);
  return handleJson<JobOut>(res);
}

// Lista todos os jobs (mais recente primeiro) pra tela "Meus cortes".
export async function listJobs(): Promise<JobSummary[]> {
  const res = await fetch(`${API_URL}/jobs`);
  return handleJson<JobSummary[]>(res);
}

export async function listClips(jobId: string): Promise<ClipOut[]> {
  const res = await fetch(`${API_URL}/jobs/${jobId}/clips`);
  return handleJson<ClipOut[]>(res);
}

export function clipFileUrl(clipId: string): string {
  return `${API_URL}/clips/${clipId}/file`;
}

export function downloadAllUrl(jobId: string): string {
  return `${API_URL}/jobs/${jobId}/download`;
}

// Reordena os clipes do job (drag-and-drop na timeline) — `clipIds` precisa
// conter TODOS os clipes do job, na nova ordem desejada (ver validação em
// PUT /jobs/{id}/clips/order no backend).
export async function reorderClips(jobId: string, clipIds: string[]): Promise<ClipOut[]> {
  const res = await fetch(`${API_URL}/jobs/${jobId}/clips/order`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clip_ids: clipIds }),
  });
  return handleJson<ClipOut[]>(res);
}

export interface CompileResult {
  path: string;
  download_url: string;
}

// Gera o vídeo compilado a partir de um subconjunto (ou todos) dos clipes do
// job, na ordem passada em `clipIds` — não precisa reprocessar nada, só
// concatena os .mp4 já renderizados (ver POST /jobs/{id}/compile no backend).
export async function compileClips(jobId: string, clipIds: string[]): Promise<CompileResult> {
  const res = await fetch(`${API_URL}/jobs/${jobId}/compile`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ clip_ids: clipIds }),
  });
  return handleJson<CompileResult>(res);
}

// URL do vídeo compilado mais recente do job (GET /jobs/{id}/compiled) — 404
// se ainda não foi gerado nenhum compilado pra esse job.
export function compiledVideoUrl(jobId: string): string {
  return `${API_URL}/jobs/${jobId}/compiled`;
}

// Vídeo ORIGINAL (não o clipe já cortado) — usado só pelo editor, pra dar de
// arrastar a linha do tempo e ver o frame real de qualquer ponto do vídeo
// (o servidor já responde a "Range", o navegador busca só o pedaço que
// precisa em vez do arquivo inteiro).
export function originalVideoUrl(jobId: string): string {
  return `${API_URL}/jobs/${jobId}/original`;
}

export function metricsUiUrl(clipId: string): string {
  return `${API_URL}/ui/metrics.html?clip_id=${clipId}`;
}

export interface ClipEditOptions {
  start_time?: number;
  end_time?: number;
  captions_enabled?: boolean;
  caption_font_size?: number;
  caption_color?: string;
  caption_style?: CaptionStyle;
  caption_font_family?: CaptionFontFamily;
}

// Edita um clipe já gerado — tempo do corte e/ou estilo da legenda. Todo
// campo é opcional: omitido mantém o que o clipe/job já tinha (ver
// RecutRequest no backend, app/routers/clips.py).
export async function recutClip(clipId: string, options: ClipEditOptions): Promise<ClipOut> {
  const res = await fetch(`${API_URL}/clips/${clipId}/recut`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(options),
  });
  return handleJson<ClipOut>(res);
}

// Zoom/crop manual por trecho (ver app/routers/pan.py) — sobrescreve o
// rastreamento automático de rosto pra vídeo solo. Mandar `keyframes: []`
// remove o override e volta pra detecção automática.
export async function getClipPan(clipId: string): Promise<PanKeyframesOut> {
  const res = await fetch(`${API_URL}/clips/${clipId}/pan`);
  return handleJson<PanKeyframesOut>(res);
}

export async function setClipPan(clipId: string, keyframes: PanKeyframe[]): Promise<ClipOut> {
  const res = await fetch(`${API_URL}/clips/${clipId}/pan`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keyframes }),
  });
  return handleJson<ClipOut>(res);
}

// Legenda editável por clipe (ver app/routers/captions.py) — corrige o
// TEXTO que o Whisper errou sem precisar reprocessar o vídeo inteiro.
// Mandar `segments: []` remove o override e volta pra transcrição original.
export async function getClipCaptions(clipId: string): Promise<CaptionLinesOut> {
  const res = await fetch(`${API_URL}/clips/${clipId}/captions`);
  return handleJson<CaptionLinesOut>(res);
}

export async function setClipCaptions(clipId: string, segments: CaptionLine[]): Promise<ClipOut> {
  const res = await fetch(`${API_URL}/clips/${clipId}/captions`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ segments }),
  });
  return handleJson<ClipOut>(res);
}

// Marca d'água/logo — uma imagem só, global (ver app/routers/watermark.py).
export async function getWatermarkStatus(): Promise<WatermarkStatus> {
  const res = await fetch(`${API_URL}/watermark`);
  return handleJson<WatermarkStatus>(res);
}

export async function uploadWatermark(file: File): Promise<WatermarkStatus> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${API_URL}/watermark`, { method: "POST", body: form });
  return handleJson<WatermarkStatus>(res);
}

export async function deleteWatermark(): Promise<WatermarkStatus> {
  const res = await fetch(`${API_URL}/watermark`, { method: "DELETE" });
  return handleJson<WatermarkStatus>(res);
}

export function watermarkPreviewUrl(): string {
  // cache-bust simples: sem isso o <img> não atualiza ao trocar de logo,
  // já que a URL do arquivo não muda de nome.
  return `${API_URL}/watermark/file?t=${Date.now()}`;
}
