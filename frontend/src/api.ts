// Cliente HTTP simples com fetch nativo — sem axios/react-query, conforme spec.
import type {
  CaptionFontFamily,
  CaptionLine,
  CaptionLinesOut,
  CaptionStyle,
  ApiErrorDetail,
  ClipCaptionStyle,
  ClipDraft,
  ClipEditIn,
  ClipEditOut,
  ClipOut,
  EditChange,
  CutSegment,
  CutSegmentsOut,
  JobOut,
  JobSummary,
  PanKeyframe,
  PanKeyframesOut,
  RelaxingVideoOut,
  UploadConfig,
  UploadResponse,
  WordsOut,
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

/**
 * Erro de API com o `detail` estruturado acessível pro chamador. Estende
 * Error de propósito: todo o código antigo faz `err.message` e continua
 * funcionando igual. O envelope novo (contrato §0.2) é
 * `{"detail": {"code", "message", ...extras}}`; os erros antigos são
 * `{"detail": "texto"}` e o 422 do FastAPI é `{"detail": [...]}` — nesses
 * dois casos `code`/`detail` ficam null e só a `message` vale.
 */
export class ApiError extends Error {
  status: number;
  code: string | null;
  detail: ApiErrorDetail | null;

  constructor(status: number, message: string, detail: ApiErrorDetail | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = detail?.code ?? null;
    this.detail = detail;
  }
}

function isErrorEnvelope(value: unknown): value is ApiErrorDetail {
  return (
    typeof value === "object" &&
    value !== null &&
    !Array.isArray(value) &&
    typeof (value as { code?: unknown }).code === "string"
  );
}

// Monta o ApiError a partir do corpo já lido (ou null se não era JSON).
function apiErrorFromBody(res: Response, body: unknown): ApiError {
  const raw = (body as { detail?: unknown } | null)?.detail;
  if (isErrorEnvelope(raw)) {
    return new ApiError(res.status, raw.message || res.statusText, raw);
  }
  const message = raw == null ? res.statusText : typeof raw === "string" ? raw : JSON.stringify(raw);
  return new ApiError(res.status, message, null);
}

async function readJsonOrNull(res: Response): Promise<unknown> {
  try {
    return await res.json();
  } catch {
    // resposta sem corpo JSON (ex: erro de proxy) — quem chama usa statusText
    return null;
  }
}

async function handleJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    throw apiErrorFromBody(res, await readJsonOrNull(res));
  }
  return (await res.json()) as T;
}

// Defaults fixos do estilo (spec §2.1: Clip > Job > default). Usados só
// quando o backend ainda não manda `caption_style` no ClipOut.
export const DEFAULT_CAPTION_STYLE: ClipCaptionStyle = {
  enabled: true,
  font_size: 8,
  color: "#FFFFFF",
  preset: "bold_outline",
  font_family: "DejaVu Sans",
};

/**
 * Compatibilidade com o backend ANTERIOR ao Agente B: o ClipOut antigo não
 * tem `caption_style` nem `revision`. Em vez de espalhar `?.` pelo app
 * inteiro, todo ClipOut que entra pelo api.ts passa por aqui e sai com os
 * dois campos garantidos. Quando o backend novo estiver no ar isso vira
 * no-op (os campos já vêm preenchidos) — pode ficar ou ser removido.
 */
function normalizeClipOut(raw: ClipOut): ClipOut {
  const partial = raw as Partial<ClipOut> & ClipOut;
  const clip: ClipOut = {
    ...partial,
    caption_style: { ...DEFAULT_CAPTION_STYLE, ...(partial.caption_style ?? {}) },
    revision: typeof partial.revision === "number" ? partial.revision : 0,
  };
  mockRememberClip(clip); // MOCK — ver bloco no fim do arquivo
  return clip;
}

async function handleClip(res: Response): Promise<ClipOut> {
  return normalizeClipOut(await handleJson<ClipOut>(res));
}

async function handleClipList(res: Response): Promise<ClipOut[]> {
  return (await handleJson<ClipOut[]>(res)).map(normalizeClipOut);
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
  form.append("use_relaxing_video_fallback", String(config.use_relaxing_video_fallback));
  form.append("profanity_filter", config.profanity_filter);

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
      use_relaxing_video_fallback: config.use_relaxing_video_fallback,
      profanity_filter: config.profanity_filter,
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
  return handleClipList(res);
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
  return handleClipList(res);
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
  return handleClip(res);
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
  return handleClip(res);
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
  return handleClip(res);
}

// Cortes internos — remove trecho(s) do MEIO do clipe (ver app/routers/
// clip_cuts.py). `keep_segments: []` remove o override e volta ao clipe
// normal (sem cortes internos).
export async function getClipCuts(clipId: string): Promise<CutSegmentsOut> {
  const res = await fetch(`${API_URL}/clips/${clipId}/cuts`);
  return handleJson<CutSegmentsOut>(res);
}

export async function setClipCuts(clipId: string, keepSegments: CutSegment[]): Promise<ClipOut> {
  const res = await fetch(`${API_URL}/clips/${clipId}/cuts`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ keep_segments: keepSegments }),
  });
  return handleClip(res);
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

// Biblioteca de vídeos "relaxantes" de fundo (ver app/routers/relaxing_videos.py).
export async function listRelaxingVideos(): Promise<RelaxingVideoOut[]> {
  const res = await fetch(`${API_URL}/relaxing-videos`);
  return handleJson<RelaxingVideoOut[]>(res);
}

// Retorna na hora com status="downloading" (202) — o download roda em
// background no worker Celery. Ver getRelaxingVideo pra fazer polling.
export async function addRelaxingVideo(url: string): Promise<RelaxingVideoOut> {
  const res = await fetch(`${API_URL}/relaxing-videos`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ url }),
  });
  return handleJson<RelaxingVideoOut>(res);
}

// Consulta o status de UM vídeo relaxante — usado pra fazer polling depois
// do addRelaxingVideo, até status virar "ready" ou "failed".
export async function getRelaxingVideo(id: string): Promise<RelaxingVideoOut> {
  const res = await fetch(`${API_URL}/relaxing-videos/${id}`);
  return handleJson<RelaxingVideoOut>(res);
}

export async function deleteRelaxingVideo(id: string): Promise<void> {
  const res = await fetch(`${API_URL}/relaxing-videos/${id}`, { method: "DELETE" });
  await handleJson<{ deleted: string }>(res);
}

// ===========================================================================
// Timeline de Revisão v1 — endpoints novos (api-contract-timeline-v1.md §1-3).
// Tempos ABSOLUTOS em todos eles. Erros chegam como ApiError com `code`.
// ===========================================================================

/**
 * O backend ainda pode não ter os endpoints novos (o Agente A implementa
 * depois). A rota inexistente no FastAPI responde 404 `{"detail":"Not Found"}`
 * (string) ou 405 — diferente do 404 do contrato, que vem no envelope com
 * `code: "not_found"`. Só o primeiro caso cai no mock.
 */
function isMissingEndpoint(res: Response, body: unknown): boolean {
  if (res.status === 405) return true;
  if (res.status !== 404) return false;
  return !isErrorEnvelope((body as { detail?: unknown } | null)?.detail);
}

// GET /clips/{id}/draft — estado completo do clipe numa chamada só (§2).
export async function getClipDraft(clipId: string): Promise<ClipDraft> {
  apiMockState.draft = false; // MOCK
  const res = await fetch(`${API_URL}/clips/${clipId}/draft`);
  if (!res.ok) {
    const body = await readJsonOrNull(res);
    if (isMissingEndpoint(res, body)) return mockGetClipDraft(clipId); // MOCK
    throw apiErrorFromBody(res, body);
  }
  return (await res.json()) as ClipDraft;
}

// PUT /clips/{id}/edit — rascunho completo, UMA renderização (§1).
// `dryRun` vira `?dry_run=true`: valida e calcula sem renderizar nem gravar.
export async function editClip(
  clipId: string,
  body: ClipEditIn,
  options: { dryRun?: boolean } = {},
): Promise<ClipEditOut> {
  apiMockState.edit = false; // MOCK
  const query = options.dryRun ? "?dry_run=true" : "";
  const res = await fetch(`${API_URL}/clips/${clipId}/edit${query}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    const errBody = await readJsonOrNull(res);
    if (isMissingEndpoint(res, errBody)) return mockEditClip(clipId, body, !!options.dryRun); // MOCK
    throw apiErrorFromBody(res, errBody);
  }
  const out = (await res.json()) as ClipEditOut;
  return { ...out, clip: normalizeClipOut(out.clip) };
}

// GET /jobs/{id}/words — palavras pro snap, já censuradas (§3). Janela
// máxima de 600s no backend; a da v1 (clipe + 20s de folga) cabe numa só.
export async function getJobWords(jobId: string, from?: number, to?: number): Promise<WordsOut> {
  apiMockState.words = false; // MOCK
  const params = new URLSearchParams();
  if (from != null) params.set("from", String(from));
  if (to != null) params.set("to", String(to));
  const qs = params.toString();
  const res = await fetch(`${API_URL}/jobs/${jobId}/words${qs ? `?${qs}` : ""}`);
  if (!res.ok) {
    const body = await readJsonOrNull(res);
    if (isMissingEndpoint(res, body)) return mockGetJobWords(jobId, from, to); // MOCK
    throw apiErrorFromBody(res, body);
  }
  return (await res.json()) as WordsOut;
}

// ===========================================================================
// MOCK TEMPORÁRIO — enquanto o backend não tem /draft, /edit e /words.
//
// Por que existe: o frontend (Agente C) é feito em paralelo com o backend,
// contra o contrato. Cada função acima só cai aqui quando a ROTA não existe
// (404 sem envelope ou 405); qualquer erro real do contrato passa direto.
// Os objetos montados usam exatamente os nomes do contrato.
//
// COMO REMOVER (quando o Agente A subir os endpoints):
//   1. apagar este bloco inteiro, até o fim do arquivo;
//   2. nas 3 funções acima, apagar as linhas marcadas com `// MOCK`;
//   3. em normalizeClipOut, apagar a chamada `mockRememberClip(clip)`;
//   4. no ClipEditorScreen, apagar os usos de `apiMockState` (aviso de
//      "dados simulados").
// ===========================================================================

/** Quais endpoints novos caíram no mock na última chamada — a tela usa só
 * pra mostrar um aviso de "dados simulados", nunca pra decidir regra. */
export const apiMockState = { draft: false, edit: false, words: false };

// Não existe GET /clips/{id} no backend antigo. Como todo ClipOut passa por
// normalizeClipOut (o listClips roda antes de abrir o editor), guardamos o
// último de cada clipe aqui pra o mock do /draft saber start/end/job.
const mockClipCache = new Map<string, ClipOut>();

function mockRememberClip(clip: ClipOut): void {
  mockClipCache.set(clip.id, clip);
}

const r3 = (x: number) => Math.round(x * 1000) / 1000;

function mockNotFound(): ApiError {
  return new ApiError(404, "Corte não encontrado.", { code: "not_found", message: "Corte não encontrado." });
}

// Monta o ClipDraft a partir dos endpoints ANTIGOS (relativos ao clipe).
// A conversão relativo → absoluto aqui é exceção do mock: no fluxo real
// ela é só do backend (spec §1.1).
async function mockGetClipDraft(clipId: string): Promise<ClipDraft> {
  apiMockState.draft = true;
  const clip = mockClipCache.get(clipId);
  if (!clip) return Promise.reject(mockNotFound());
  const base = clip.start_time;
  const duration = clip.end_time - clip.start_time;
  const [caps, pan, cuts, job] = await Promise.all([
    getClipCaptions(clipId).catch(() => null),
    getClipPan(clipId).catch(() => null),
    getClipCuts(clipId).catch(() => null),
    getJobStatus(clip.job_id).catch(() => null),
  ]);

  // Buracos de cut_segments = trechos removidos (mesma derivação do §2.4,
  // sem o passo de subtrair o filtro, que o mock não sabe calcular).
  const removed: { start: number; end: number }[] = [];
  if (cuts?.is_active) {
    let cursor = 0;
    for (const [s, e] of cuts.keep_segments) {
      if (s - cursor >= 0.05) removed.push({ start: r3(base + cursor), end: r3(base + s) });
      cursor = Math.max(cursor, e);
    }
    if (duration - cursor >= 0.05) removed.push({ start: r3(base + cursor), end: r3(base + duration) });
  }

  return Promise.resolve<ClipDraft>({
    clip_id: clip.id,
    job_id: clip.job_id,
    revision: clip.revision,
    video_duration: clip.video_duration,
    has_streamer_region: clip.has_streamer_region,
    range: { start: r3(clip.start_time), end: r3(clip.end_time) },
    caption_style: { ...clip.caption_style },
    captions: {
      mode: caps?.is_manual ? "manual" : "auto",
      lines: (caps?.lines ?? []).map((l) => ({ start: r3(base + l.start), end: r3(base + l.end), text: l.text })),
    },
    pan: pan?.is_manual ? pan.keyframes.map((k) => ({ t: r3(base + k.t), cx: k.cx })) : [],
    removed,
    // O mock não recalcula os palavrões: só informa o modo do job.
    profanity: { mode: job?.profanity_filter ?? "off", ranges: [] },
  });
}

// Simula o /edit SEM renderizar: aplica a regra de perdas/ajustes por
// geometria simples (pra dar de testar o modal de perdas) e devolve o
// rascunho como ficaria. Não grava nada, não muda `revision`.
function mockEditClip(clipId: string, body: ClipEditIn, dryRun: boolean): Promise<ClipEditOut> {
  apiMockState.edit = true;
  const clip = mockClipCache.get(clipId);
  if (!clip) return Promise.reject(mockNotFound());
  const { start, end } = body.range;
  const overlaps = (a: number, b: number) => b > start && a < end;
  const losses: EditChange[] = [];
  const adjustments: EditChange[] = [];

  const lines =
    body.captions.mode === "manual"
      ? body.captions.lines.flatMap((l) => {
          if (!overlaps(l.start, l.end)) {
            losses.push({ kind: "caption", start: l.start, end: l.end, text: l.text });
            return [];
          }
          const s = Math.max(start, l.start);
          const e = Math.min(end, l.end);
          if (s !== l.start || e !== l.end) {
            adjustments.push({ kind: "caption", start: s, end: e, text: l.text, change: "cortada na borda (simulado)" });
          }
          return [{ start: r3(s), end: r3(e), text: l.text }];
        })
      : body.captions.lines.filter((l) => overlaps(l.start, l.end));

  const pan = body.pan.filter((p) => {
    const inside = p.t >= start && p.t <= end; // borda conta como dentro (spec §4)
    if (!inside) losses.push({ kind: "pan", start: p.t, end: p.t });
    return inside;
  });

  const removed = body.removed.flatMap((r) => {
    if (!overlaps(r.start, r.end)) {
      losses.push({ kind: "removed", start: r.start, end: r.end });
      return [];
    }
    return [{ start: r3(Math.max(start, r.start)), end: r3(Math.min(end, r.end)) }];
  });

  if (losses.length > 0 && !body.confirm_losses) {
    const message = `${losses.length} itens ficaram fora do novo corte e serão descartados.`;
    return Promise.reject(
      new ApiError(409, message, { code: "losses_require_confirmation", message, losses }),
    );
  }

  return Promise.resolve<ClipEditOut>({
    status: dryRun ? "dry_run" : "rendered",
    clip,
    draft: {
      clip_id: clip.id,
      job_id: clip.job_id,
      revision: body.revision,
      video_duration: clip.video_duration,
      has_streamer_region: clip.has_streamer_region,
      range: { start: r3(start), end: r3(end) },
      caption_style: { ...body.caption_style },
      captions: { mode: body.captions.mode, lines },
      pan,
      removed,
      profanity: { mode: "off", ranges: [] },
    },
    adjustments,
    render_seconds: null, // nada foi renderizado de verdade
  });
}

// Sem /words no backend, a regra da spec §8 já é "snap desligado em
// silêncio" — então o mock devolve a resposta 200 válida com `words: []`
// (contrato §3.4) em vez de inventar palavras falsas pra encaixar.
function mockGetJobWords(jobId: string, from?: number, to?: number): Promise<WordsOut> {
  apiMockState.words = true;
  const f = from ?? 0;
  return Promise.resolve<WordsOut>({
    job_id: jobId,
    language: null,
    from: f,
    to: to ?? f + 600,
    truncated: false,
    censored: false,
    words: [],
  });
}
