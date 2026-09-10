// Tipos espelhando os schemas Pydantic do backend (app/schemas.py).

export interface VideoOut {
  id: string;
  filename: string;
  duration: number | null;
  status: string;
  created_at: string;
}

export type JobStatus = "pending" | "running" | "completed" | "failed";

export type WatermarkPosition = "top-left" | "top-right" | "bottom-left" | "bottom-right";

export type CaptionStyle = "bold_outline" | "boxed" | "minimal" | "no_outline";

export type CaptionFontFamily =
  | "DejaVu Sans"
  | "DejaVu Serif"
  | "DejaVu Sans Mono"
  | "Liberation Sans"
  | "Liberation Serif"
  | "Liberation Mono";

export interface JobOut {
  id: string;
  video_id: string;
  status: JobStatus;
  progress: number;
  current_phase: string | null;
  logs: string;
  error_message: string | null;
  max_clips: number;
  clip_duration_min: number;
  clip_duration_max: number;
  keywords: string | null;
  language: string | null;
  watermark_enabled: boolean;
  watermark_position: WatermarkPosition | null;
  captions_enabled: boolean;
  caption_font_size: number;
  caption_color: string;
  caption_style: CaptionStyle;
  caption_font_family: CaptionFontFamily;
  created_at: string;
  updated_at: string;
}

export interface UploadResponse {
  video: VideoOut;
  job: JobOut;
}

// Espelha app.schemas.JobSummary (GET /jobs) — resumo pra tela "Meus cortes".
export interface JobSummary {
  id: string;
  status: JobStatus;
  progress: number;
  current_phase: string | null;
  created_at: string;
  video_filename: string;
  video_duration: number | null;
  clip_count: number;
}

export type Platform =
  | "tiktok"
  | "instagram_reels"
  | "youtube_shorts"
  | "facebook_reels";

export type HashtagsByPlatform = Partial<Record<Platform, string[]>>;

export interface ClipOut {
  id: string;
  video_id: string;
  job_id: string;
  index: number;
  start_time: number;
  end_time: number;
  score: number;
  processed_path: string | null;
  transcript_text: string | null;
  hashtags: HashtagsByPlatform | null;
  video_duration: number | null;
  has_streamer_region: boolean;
}

export interface MetricIn {
  platform: Platform | string;
  views: number;
  likes: number;
  comments: number;
  shares: number;
}

export interface MetricOut extends MetricIn {
  id: string;
  clip_id: string;
  source: string;
  external_video_id: string | null;
  recorded_at: string;
}

// Zoom/crop manual por trecho (ver POST /clips/{id}/pan no backend) — cada
// keyframe é "no segundo t (desde o início do CLIPE), foca em cx (0..1 da
// largura do vídeo ORIGINAL)". Sobrescreve o rastreamento automático de
// rosto pra vídeo solo (sem streamer_region).
export interface PanKeyframe {
  t: number;
  cx: number;
}

export interface PanKeyframesOut {
  keyframes: PanKeyframe[];
  is_manual: boolean;
}

// Edição manual do TEXTO da legenda (ver GET/PUT /clips/{id}/captions no
// backend) — cada linha em segundos relativos ao início do CLIPE (0 =
// start_time do clipe), igual convenção de PanKeyframe.t.
export interface CaptionLine {
  start: number;
  end: number;
  text: string;
}

export interface CaptionLinesOut {
  lines: CaptionLine[];
  is_manual: boolean; // false = linhas vieram da transcrição automática do Whisper (sem override salvo)
}

// Cortes internos (ver PUT /clips/{id}/cuts no backend, app/routers/clip_cuts.py)
// — cada item é [início, fim] em segundos, relativos ao início do CLIPE
// ATUAL (0 = clip.start_time), representando um trecho que deve ser
// MANTIDO. O restante (fora desses pares) vira um "buraco" removido do
// vídeo final. Par (tupla), não objeto — bate 1:1 com o formato aceito/
// devolvido pela API e com Clip.cut_segments no backend.
export type CutSegment = [number, number];

export interface CutSegmentsOut {
  keep_segments: CutSegment[];
  is_active: boolean; // false = sem cortes internos (removidos ou nunca configurados)
}

export interface UploadConfig {
  max_clips: number;
  clip_duration_min: number;
  clip_duration_max: number;
  keywords: string;
  language: string;
  watermark_enabled: boolean;
  watermark_position: WatermarkPosition;
  captions_enabled: boolean;
  caption_font_size: number;
  caption_color: string;
  caption_style: CaptionStyle;
  caption_font_family: CaptionFontFamily;
}
