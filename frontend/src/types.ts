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
