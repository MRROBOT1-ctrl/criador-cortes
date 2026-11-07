import { useEffect, useRef, useState } from "react";
import {
  ApiError,
  apiMockState,
  clipFileUrl,
  DEFAULT_CAPTION_STYLE,
  editClip,
  getClipDraft,
  getJobWords,
  originalVideoUrl,
  recutClip,
} from "../api";
import type {
  CaptionFontFamily,
  CaptionStyle,
  ClipCaptionStyle,
  ClipDraft,
  ClipEditIn,
  ClipOut,
  EditChange,
  WordOut,
} from "../types";
import { ReviewTimeline } from "./ReviewTimeline";
import { CaptionSection } from "./CaptionSection";
import { CaptionTextEditor } from "./CaptionTextEditor";
import { CutSegmentsEditor } from "./CutSegmentsEditor";
import { ManualPanEditor } from "./ManualPanEditor";
import { TimelineScrubber } from "./TimelineScrubber";
import { Badge, Button, Card, cn, FOCUS_VISIBLE_RING, MOTION_SAFE_TRANSITION, Spinner } from "./ui";

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
  { id: "revisao", label: "Revisão (beta)" },
] as const;
type TabId = (typeof TABS)[number]["id"];

interface Props {
  clip: ClipOut;
  onBack: () => void;
  onUpdated: (clip: ClipOut) => void;
}

// Estilo efetivo do clipe -> campos da aba antiga. ANTES os campos nasciam
// com valores fixos (true/8/#FFFFFF/bold_outline/DejaVu Sans), e como o
// `recut` agora GRAVA os 5 campos de estilo enviados (spec §6), qualquer
// "Aplicar" sobrescreveria o estilo salvo do clipe com esses fixos. O
// `?? DEFAULT_CAPTION_STYLE` cobre um ClipOut que, por algum caminho, chegue
// sem o campo (o api.ts já normaliza, mas a tela não deve quebrar por isso).
function styleFromClip(clip: ClipOut) {
  const cs: ClipCaptionStyle = clip.caption_style ?? DEFAULT_CAPTION_STYLE;
  return {
    captionsEnabled: cs.enabled,
    captionFontSize: cs.font_size,
    captionColor: cs.color,
    // `preset` do ClipCaptionStyle é o `caption_style` do RecutRequest
    // (contrato §4.5) — mesma lista de valores, nome diferente.
    captionStyle: cs.preset as CaptionStyle,
    captionFontFamily: cs.font_family as CaptionFontFamily,
  };
}

/**
 * Tela cheia de edição de um clipe — cada aba corresponde a UM endpoint do
 * backend (recut / pan / captions / cuts), então só a aba ativa busca/
 * renderiza suas próprias coisas por vez (antes, no modal, as 5 seções
 * ficavam todas montadas e buscando dado ao mesmo tempo numa área pequena).
 */
export function ClipEditorScreen({ clip, onBack, onUpdated }: Props) {
  const [tab, setTab] = useState<TabId>("corte");
  // A aba de revisão é montada na primeira visita e depois só escondida —
  // senão trocar de aba jogaria fora o rascunho local não renderizado.
  const [reviewMounted, setReviewMounted] = useState(false);
  const [reviewDirty, setReviewDirty] = useState(false);

  const initialStyle = styleFromClip(clip);
  const [start, setStart] = useState(clip.start_time);
  const [end, setEnd] = useState(clip.end_time);
  const [captionsEnabled, setCaptionsEnabled] = useState(initialStyle.captionsEnabled);
  const [captionFontSize, setCaptionFontSize] = useState(initialStyle.captionFontSize);
  const [captionColor, setCaptionColor] = useState(initialStyle.captionColor);
  const [captionStyle, setCaptionStyle] = useState<CaptionStyle>(initialStyle.captionStyle);
  const [captionFontFamily, setCaptionFontFamily] = useState<CaptionFontFamily>(initialStyle.captionFontFamily);
  const [applying, setApplying] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [sourcePreviewFailed, setSourcePreviewFailed] = useState(false);

  const sourceVideoRef = useRef<HTMLVideoElement>(null);

  // Snapshot dos campos da aba "Corte & estilo" logo que o clipe é carregado
  // (ou trocado) — base de comparação pra saber se há "mudanças não salvas"
  // (Parte 5). Um ref (não state) de propósito: só precisa ser lido durante
  // o render pra calcular `isDirty`, escrevê-lo não deve disparar outro
  // render. Só essa aba entra aqui: as outras 3 (Zoom manual/Legenda/
  // Remover trechos) já aplicam ação a ação com seus próprios botões, sem um
  // estado combinado de "vários campos, um Aplicar só" pra rastrear.
  const initialFormRef = useRef({
    start: clip.start_time,
    end: clip.end_time,
    captionsEnabled,
    captionFontSize,
    captionColor,
    captionStyle,
    captionFontFamily,
  });

  // Se o usuário reabrir a edição de um clipe diferente sem desmontar o
  // componente (ex: navegação futura entre clipes), os campos de tempo/
  // estilo precisam refletir o NOVO clipe, não ficar presos no anterior — e
  // o snapshot de "limpo" precisa andar junto, senão o clipe novo nasceria
  // marcado como "sujo" por comparação com o snapshot do clipe anterior.
  //
  // Também ressincroniza quando a `revision` do MESMO clipe muda (ex: a aba
  // "Revisão (beta)" renderizou com outro corte/estilo) e a aba antiga não
  // tem edição pendente — senão o próximo "Aplicar" aqui mandaria o corte e
  // o estilo velhos e desfaria a revisão. Com edição pendente, não mexe: o
  // usuário perderia o que digitou. O estilo agora vem de clip.caption_style
  // (antes ficava preso no valor fixo inicial).
  const lastClipIdRef = useRef(clip.id);
  useEffect(() => {
    const clipChanged = lastClipIdRef.current !== clip.id;
    lastClipIdRef.current = clip.id;
    if (!clipChanged && isDirty) return;
    const style = styleFromClip(clip);
    setStart(clip.start_time);
    setEnd(clip.end_time);
    setCaptionsEnabled(style.captionsEnabled);
    setCaptionFontSize(style.captionFontSize);
    setCaptionColor(style.captionColor);
    setCaptionStyle(style.captionStyle);
    setCaptionFontFamily(style.captionFontFamily);
    if (clipChanged) {
      setTab("corte");
      setReviewMounted(false);
      setReviewDirty(false);
    }
    initialFormRef.current = { start: clip.start_time, end: clip.end_time, ...style };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.id, clip.revision]);

  const isDirty =
    start !== initialFormRef.current.start ||
    end !== initialFormRef.current.end ||
    captionsEnabled !== initialFormRef.current.captionsEnabled ||
    captionFontSize !== initialFormRef.current.captionFontSize ||
    captionColor !== initialFormRef.current.captionColor ||
    captionStyle !== initialFormRef.current.captionStyle ||
    captionFontFamily !== initialFormRef.current.captionFontFamily;

  const windowStart = Math.max(0, clip.start_time - WINDOW_PADDING_SECONDS);
  const windowEnd =
    clip.video_duration != null
      ? Math.min(clip.video_duration, clip.end_time + WINDOW_PADDING_SECONDS)
      : clip.end_time + WINDOW_PADDING_SECONDS;

  // Sai da tela de edição — se a aba "Corte & estilo" tem mudança não
  // aplicada, confirma antes (window.confirm nativo: decisão explícita de
  // manter a Parte 5 100% visual, sem introduzir um modal customizado novo).
  //
  // IMPORTANTE — por que não existe nenhum código de "descartar mudanças"
  // aqui: `onBack` sobe pro App.tsx, que troca `stage` e desmonta esse
  // componente inteiro (o `<main key={stage}>` de App.tsx força remontar a
  // cada troca de estágio). Como nada foi de fato aplicado no backend, o
  // `clip` prop não mudou — reabrir o editor desse mesmo clipe cria uma
  // instância NOVA, lendo os valores originais direto do `clip`. O
  // `isDirty`/`initialFormRef` daqui só existem pra avisar ANTES de sair
  // (via este confirm) — não fazem parte de "restaurar" nada depois. Se um
  // dia esse componente passar a ficar montado entre navegações (ex: um
  // cache de abas), esse comportamento de "esquecer" a edição descartada
  // deixa de ser automático e precisaria de um reset explícito aqui.
  function requestBack() {
    if (isDirty && !window.confirm("Você tem alterações não salvas em \"Corte & estilo\". Sair mesmo assim?")) {
      return;
    }
    // Rascunho da aba de revisão também conta (spec §7, estado "Editando":
    // sair da tela pede confirmação).
    if (reviewDirty && !window.confirm("Você tem um rascunho não renderizado em \"Revisão (beta)\". Sair mesmo assim?")) {
      return;
    }
    onBack();
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") requestBack();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDirty, reviewDirty, onBack]);

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
      // Aplicado com sucesso — os valores atuais viram o novo "limpo"
      // (senão a barra continuaria acusando mudança não salva depois de já
      // ter salvo).
      initialFormRef.current = {
        start,
        end,
        captionsEnabled,
        captionFontSize,
        captionColor,
        captionStyle,
        captionFontFamily,
      };
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
        <Button variant="ghost" onClick={requestBack}>
          ← Voltar pros cortes
        </Button>
        <h2 className="text-xl font-semibold text-text">Editando corte #{clip.index + 1}</h2>
      </div>

      <div className="flex gap-1 overflow-x-auto rounded-lg border border-border-strong bg-surface-hover p-1">
        {TABS.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => {
              setTab(t.id);
              if (t.id === "revisao") setReviewMounted(true);
            }}
            className={cn(
              "flex-1 whitespace-nowrap rounded-md px-3 py-1.5 text-sm font-medium",
              MOTION_SAFE_TRANSITION,
              FOCUS_VISIBLE_RING,
              "active:brightness-90 motion-safe:active:scale-[0.98]",
              tab === t.id ? "bg-accent text-white" : "text-text-muted hover:text-heading",
            )}
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
      <Card padding="md" hidden={tab !== "corte" && tab !== "pan"}>
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
            <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-bg p-2 text-center text-xs text-text-subtle">
              Prévia do vídeo original indisponível pra esse formato — os campos abaixo continuam
              funcionando normalmente.
            </div>
          )}
        </div>
      </Card>

      {reviewMounted && (
        <ReviewTab
          clip={clip}
          active={tab === "revisao"}
          onUpdated={onUpdated}
          onDirtyChange={setReviewDirty}
        />
      )}

      <Card padding="md" hidden={tab === "revisao"}>
        {tab === "corte" && (
          <div className="flex flex-col gap-5">
            <section className="flex flex-col gap-3">
              <p className="text-sm font-medium text-text-secondary">Ajustar o corte</p>
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
                  className={cn(
                    "w-full rounded-md border border-border-strong bg-surface-hover px-2 py-1 text-xs text-text",
                    MOTION_SAFE_TRANSITION,
                    "outline-none focus:border-accent focus:[--tw-outline-style:solid] focus:outline-2 focus:outline-accent focus:outline-offset-2",
                  )}
                  aria-label="Início (s)"
                />
                <span className="text-xs text-text-subtle">até</span>
                <input
                  type="number"
                  step="0.1"
                  value={end.toFixed(2)}
                  onChange={(e) => setEnd(Number(e.target.value))}
                  className={cn(
                    "w-full rounded-md border border-border-strong bg-surface-hover px-2 py-1 text-xs text-text",
                    MOTION_SAFE_TRANSITION,
                    "outline-none focus:border-accent focus:[--tw-outline-style:solid] focus:outline-2 focus:outline-accent focus:outline-offset-2",
                  )}
                  aria-label="Fim (s)"
                />
              </div>
            </section>

            <section className="flex flex-col gap-3 border-t border-border pt-5 sm:flex-row">
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
                <p className="mb-2 text-xs text-text-subtle">
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
              <div className="rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
                {error}
              </div>
            )}
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
      </Card>

      {/* Barra de Aplicar fixa no rodapé (Parte 5) — só na aba "Corte &
          estilo", a única que combina vários campos (tempo + legenda) sob
          UM botão só. `sticky` (não `fixed`): fica dentro do fluxo normal
          da coluna max-w-4xl, sem precisar recentralizar nada, e some
          quando o conteúdo da aba é mais curto que a tela (não empurra pra
          baixo à toa). O indicador de "alterações não salvas" reflete
          `isDirty` — comparação com o snapshot tirado quando o clipe foi
          carregado (ver `initialFormRef`), não um contador que soma toda
          mudança. */}
      {tab === "corte" && (
        <div className="sticky bottom-0 z-10 -mx-4 border-t border-border bg-surface/95 px-4 py-3 backdrop-blur motion-safe:transition-colors sm:mx-0 sm:rounded-xl sm:border">
          <div className="flex items-center justify-between gap-3">
            <span className="text-xs text-text-subtle">
              {isDirty ? (
                <span className="font-medium text-warning-hover">● Alterações não salvas</span>
              ) : (
                "Tudo aplicado"
              )}
            </span>
            <Button loading={applying} onClick={handleApply}>
              {applying ? "Aplicando... (pode levar um tempinho)" : "Aplicar"}
            </Button>
          </div>
        </div>
      )}
    </div>
  );
}

// ===========================================================================
// Aba "Revisão (beta)" — Timeline de Revisão v1 (spec §7).
//
// Diferente das abas antigas (cada ajuste renderiza na hora), aqui tudo é
// editado num rascunho LOCAL, sem nenhuma chamada de API durante a edição, e
// renderizado UMA vez no PUT /edit. Todos os tempos são absolutos.
// ===========================================================================

// Folga mostrada de cada lado do corte na timeline de revisão (spec §7 e §8:
// "corte ± 30s" — também é a janela pedida ao /words). 30s e não 10s porque
// a janela fica fixa na versão salva, e 10s limitava demais quanto o corte
// podia crescer a cada renderização.
const REVIEW_PADDING_SECONDS = 30;

function formatTimecode(seconds: number): string {
  const s = Math.max(0, seconds);
  const m = Math.floor(s / 60);
  return `${m.toString().padStart(2, "0")}:${(s - m * 60).toFixed(1).padStart(4, "0")}`;
}

// Só os campos que o usuário edita — é isso que decide "● Não salvo" e é
// isso que vai pro PUT /edit (o resto do ClipDraft é só leitura).
function editableKey(d: ClipDraft): string {
  return JSON.stringify([d.range, d.caption_style, d.captions, d.pan, d.removed]);
}

function draftToEditIn(d: ClipDraft, confirmLosses: boolean): ClipEditIn {
  return {
    revision: d.revision,
    range: d.range,
    caption_style: d.caption_style,
    captions: d.captions,
    pan: d.pan,
    removed: d.removed,
    confirm_losses: confirmLosses,
  };
}

// Interpolação linear do foco horizontal entre os pontos de zoom — só pra
// desenhar a moldura 9:16 da prévia; o enquadramento real é do backend.
function panAt(pan: ClipDraft["pan"], t: number): number | null {
  if (pan.length === 0) return null;
  if (t <= pan[0].t) return pan[0].cx;
  for (let i = 1; i < pan.length; i++) {
    const a = pan[i - 1];
    const b = pan[i];
    if (t <= b.t) return a.cx + ((b.cx - a.cx) * (t - a.t)) / Math.max(0.001, b.t - a.t);
  }
  return pan[pan.length - 1].cx;
}

const LOSS_ICON: Record<EditChange["kind"], string> = { caption: "💬", pan: "◆", removed: "✂" };

type Banner =
  | { kind: "error"; message: string }
  | { kind: "conflict"; message: string }
  | { kind: "busy"; message: string }
  | { kind: "success"; renderSeconds: number | null; adjustments: EditChange[]; simulated: boolean };

interface ReviewTabProps {
  clip: ClipOut;
  active: boolean;
  onUpdated: (clip: ClipOut) => void;
  onDirtyChange: (dirty: boolean) => void;
}

function ReviewTab({ clip, active, onUpdated, onDirtyChange }: ReviewTabProps) {
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [serverDraft, setServerDraft] = useState<ClipDraft | null>(null);
  const [draft, setDraft] = useState<ClipDraft | null>(null);
  const [words, setWords] = useState<WordOut[]>([]);
  const [snapUnavailable, setSnapUnavailable] = useState(false);
  const [mocked, setMocked] = useState(false);
  const [rendering, setRendering] = useState(false);
  const [banner, setBanner] = useState<Banner | null>(null);
  const [losses, setLosses] = useState<EditChange[] | null>(null);
  const [selectedCaption, setSelectedCaption] = useState<number | null>(null);
  const [playhead, setPlayhead] = useState(clip.start_time);
  const [styleOpen, setStyleOpen] = useState(false);
  const [previewNonce, setPreviewNonce] = useState(0);
  const [videoAspect, setVideoAspect] = useState(16 / 9);
  const [frameBoxWidth, setFrameBoxWidth] = useState(0);
  const [sourceFailed, setSourceFailed] = useState(false);

  const videoRef = useRef<HTMLVideoElement>(null);
  const previewBoxRef = useRef<HTMLDivElement>(null);

  const dirty = draft !== null && serverDraft !== null && editableKey(draft) !== editableKey(serverDraft);

  useEffect(() => {
    onDirtyChange(dirty);
  }, [dirty, onDirtyChange]);

  // /words: falha ou lista vazia => snap desligado em silêncio, só com o
  // aviso discreto (spec §8). Não há repetição automática: a "nova
  // tentativa" acontece naturalmente quando o clipe é reaberto (o
  // componente monta de novo).
  function loadWords(start: number, end: number): Promise<void> {
    const from = Math.max(0, start - REVIEW_PADDING_SECONDS);
    const to = end + REVIEW_PADDING_SECONDS;
    return getJobWords(clip.job_id, from, to)
      .then((res) => {
        setWords(res.words);
        setSnapUnavailable(res.words.length === 0);
      })
      .catch(() => {
        setWords([]);
        setSnapUnavailable(true);
      });
  }

  function loadDraft(): Promise<void> {
    return getClipDraft(clip.id).then((d) => {
      setServerDraft(d);
      setDraft(d);
      setSelectedCaption(null);
      setMocked(apiMockState.draft);
    });
  }

  // Carregamento inicial: /draft e /words em PARALELO (spec §7). A janela do
  // /words sai do ClipOut que já temos, pra não precisar esperar o /draft.
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    Promise.all([
      loadDraft().catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : "Falha ao carregar o corte.");
      }),
      loadWords(clip.start_time, clip.end_time),
    ]).finally(() => !cancelled && setLoading(false));
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.id]);

  // Uma aba antiga (ou outra janela) gravou o clipe e o rascunho daqui está
  // limpo: recarrega o /draft em vez de esperar o revision_conflict no
  // Renderizar. Com rascunho sujo não mexe — o conflito avisa na hora certa
  // e o usuário decide. Não roda durante a edição (só quando `revision` muda).
  useEffect(() => {
    if (loading || rendering || dirty || !serverDraft) return;
    if (clip.revision !== serverDraft.revision) {
      loadDraft().catch(() => {
        /* fica com a versão atual; o Renderizar vai acusar o conflito */
      });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.revision]);

  // Aba escondida não deve continuar tocando o vídeo original por trás.
  useEffect(() => {
    if (!active) videoRef.current?.pause();
  }, [active]);

  // Largura real da caixa da prévia — a legenda em CSS é escalada por ela
  // (mesma calibração da prévia antiga, que foi feita pra 220px de largura).
  useEffect(() => {
    const el = previewBoxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const obs = new ResizeObserver(([entry]) => setFrameBoxWidth(entry.contentRect.width));
    obs.observe(el);
    return () => obs.disconnect();
  }, [loading]);

  function seek(t: number) {
    setPlayhead(t);
    const v = videoRef.current;
    if (v && !sourceFailed) v.currentTime = t;
  }

  function updateDraft(patch: Partial<ClipDraft>) {
    // Sem rascunho carregado ou renderizando: a timeline é só leitura.
    if (rendering) return;
    setDraft((d) => (d ? { ...d, ...patch } : d));
    // Uma edição nova invalida o aviso de sucesso/erro anterior.
    setBanner((b) => (b && b.kind === "success" ? null : b));
  }

  async function render(confirmLosses: boolean) {
    if (!draft) return;
    setLosses(null);
    setBanner(null);
    setRendering(true);
    try {
      const out = await editClip(clip.id, draftToEditIn(draft, confirmLosses));
      // Sucesso: o `draft` da resposta (normalizado e remapeado pelo
      // backend) SUBSTITUI o rascunho local — é a nova base "Tudo salvo".
      setServerDraft(out.draft);
      setDraft(out.draft);
      setSelectedCaption(null);
      onUpdated(out.clip);
      setPreviewNonce((n) => n + 1);
      setBanner({
        kind: "success",
        renderSeconds: out.render_seconds,
        adjustments: out.adjustments,
        simulated: apiMockState.edit,
      });
      // A janela da timeline anda junto com o novo corte; se o snap estava
      // funcionando, pega as palavras da janela nova (fora da edição, então
      // não conta como chamada "durante a edição").
      if (!snapUnavailable) void loadWords(out.draft.range.start, out.draft.range.end);
    } catch (err) {
      // Qualquer erro PRESERVA o rascunho (spec §7) — nada aqui mexe em `draft`.
      if (err instanceof ApiError && err.code === "losses_require_confirmation") {
        setLosses(err.detail?.losses ?? []);
      } else if (err instanceof ApiError && err.code === "revision_conflict") {
        setBanner({ kind: "conflict", message: "Este corte foi alterado em outra aba." });
      } else if (err instanceof ApiError && err.code === "render_in_progress") {
        setBanner({
          kind: "busy",
          message: "Outra edição deste corte está renderizando. Tente de novo em alguns segundos.",
        });
      } else {
        if (err instanceof ApiError && err.code === "invalid_caption" && typeof err.detail?.index === "number") {
          setSelectedCaption(err.detail.index); // já abre a linha com problema
        }
        setBanner({ kind: "error", message: err instanceof Error ? err.message : "Falha ao renderizar." });
      }
    } finally {
      setRendering(false);
    }
  }

  async function reloadAfterConflict() {
    setBanner(null);
    setLoading(true);
    try {
      await loadDraft();
      setLoadError(null);
    } catch (err) {
      setLoadError(err instanceof Error ? err.message : "Falha ao recarregar o corte.");
    } finally {
      setLoading(false);
    }
  }

  if (loading) {
    // Esqueleto das prévias e das trilhas (estado "Carregando").
    return (
      <Card padding="md" hidden={!active} className="flex flex-col gap-4" aria-busy="true">
        <div className="grid gap-4 sm:grid-cols-2">
          <div className="aspect-video animate-pulse rounded-lg bg-surface-hover" />
          <div className="mx-auto aspect-[9/16] w-40 animate-pulse rounded-lg bg-surface-hover" />
        </div>
        {[0, 1, 2, 3].map((i) => (
          <div key={i} className="h-8 animate-pulse rounded-md bg-surface-hover" />
        ))}
      </Card>
    );
  }

  if (loadError || !draft || !serverDraft) {
    return (
      <Card padding="md" hidden={!active} className="flex flex-col gap-3">
        <div className="rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
          {loadError ?? "Falha ao carregar o corte."}
        </div>
        <Button variant="secondary" size="sm" className="self-start" onClick={reloadAfterConflict}>
          Tentar de novo
        </Button>
      </Card>
    );
  }

  const duration = draft.range.end - draft.range.start;
  // Janela fixa a partir da versão SALVA (não do rascunho): se andasse junto
  // com o arrasto, a trilha deslizaria embaixo do ponteiro.
  const windowStart = Math.max(0, serverDraft.range.start - REVIEW_PADDING_SECONDS);
  const windowEnd =
    serverDraft.video_duration != null
      ? Math.min(serverDraft.video_duration, serverDraft.range.end + REVIEW_PADDING_SECONDS)
      : serverDraft.range.end + REVIEW_PADDING_SECONDS;

  // Moldura 9:16 sobre o vídeo original (largura relativa à do vídeo).
  const frameFraction = Math.min(1, 9 / 16 / videoAspect);
  const cx = panAt(draft.pan, playhead);
  const frameLeft = Math.min(1 - frameFraction, Math.max(0, (cx ?? 0.5) - frameFraction / 2));
  const framePx = frameBoxWidth * frameFraction;
  const currentLine = draft.captions.lines.find((l) => playhead >= l.start && playhead < l.end);
  const cs = draft.caption_style;
  const selectedLine = selectedCaption !== null ? draft.captions.lines[selectedCaption] : undefined;
  // Estimativa grosseira pro texto do botão (spec §12: ~10s num clipe de
  // 17s, 35–45s num de 60s). Não é promessa, só expectativa.
  const etaSeconds = Math.max(5, Math.round(duration * 0.65));

  function setCaptionText(index: number, text: string) {
    if (!draft) return;
    // Editar o texto transforma a legenda em `manual` (as falas atuais viram
    // o ponto de partida), igual ao "Salvar legendas" da aba antiga faz.
    updateDraft({
      captions: {
        mode: "manual",
        lines: draft.captions.lines.map((l, i) => (i === index ? { ...l, text } : l)),
      },
    });
  }

  function addPanPointAtPlayhead(e: React.MouseEvent<HTMLDivElement>) {
    if (!draft || rendering) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const newCx = Math.min(1, Math.max(0, (e.clientX - rect.left) / rect.width));
    const t = Math.round((videoRef.current?.currentTime ?? playhead) * 1000) / 1000;
    // Mesma regra da aba antiga: um ponto a menos de 50ms de outro substitui
    // o anterior (o contrato proíbe `t` repetido).
    const next = [...draft.pan.filter((p) => Math.abs(p.t - t) > 0.05), { t, cx: newCx }].sort((a, b) => a.t - b.t);
    updateDraft({ pan: next });
  }

  return (
    <div hidden={!active} className="flex flex-col gap-4">
      {/* Cabeçalho: corte, estado e ações */}
      <Card padding="sm" className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex flex-col">
          <span className="text-sm font-semibold text-heading">
            Corte #{clip.index + 1} · {formatTimecode(draft.range.start)} → {formatTimecode(draft.range.end)} (
            {duration.toFixed(1)}s)
          </span>
          <span className="text-xs text-text-subtle">
            {dirty ? <span className="font-medium text-warning-hover">● Não salvo</span> : "Tudo salvo"}
            {snapUnavailable && <span className="ml-2 text-text-subtle">· snap indisponível</span>}
            {/* MOCK — remover junto com o mock do api.ts */}
            {mocked && <span className="ml-2 text-warning-hover">· dados simulados (backend sem /draft)</span>}
          </span>
        </div>
        <div className="flex gap-2">
          <Button
            variant="secondary"
            size="sm"
            disabled={!dirty || rendering}
            onClick={() => {
              setDraft(serverDraft);
              setSelectedCaption(null);
              setBanner(null);
            }}
          >
            Descartar
          </Button>
          <Button size="sm" loading={rendering} disabled={!dirty || rendering} onClick={() => render(false)}>
            {rendering ? `Renderizando… ~${etaSeconds}s` : "Renderizar"}
          </Button>
        </div>
      </Card>

      {banner?.kind === "error" && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
          <span>{banner.message}</span>
          <Button variant="secondary" size="sm" onClick={() => render(false)}>
            Tentar de novo
          </Button>
        </div>
      )}
      {banner?.kind === "busy" && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-warning/50 bg-warning/10 px-3 py-2 text-sm text-warning-hover">
          <span>{banner.message}</span>
          <Button variant="secondary" size="sm" onClick={() => render(false)}>
            Tentar de novo
          </Button>
        </div>
      )}
      {banner?.kind === "conflict" && (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-danger-border bg-danger-bg/50 px-3 py-2 text-sm text-danger-text">
          {/* Na v1 não há mesclagem: recarregar descarta o rascunho local. */}
          <span>{banner.message} Recarregue para ver a versão atual (o rascunho daqui será descartado).</span>
          <Button variant="secondary" size="sm" onClick={reloadAfterConflict}>
            Recarregar
          </Button>
        </div>
      )}
      {banner?.kind === "success" && (
        <div className="rounded-lg border border-border-strong bg-success-subtle-bg/40 px-3 py-2 text-sm text-success-subtle-text">
          {banner.simulated
            ? "Simulado: o backend ainda não tem o PUT /edit, nada foi renderizado."
            : `Renderizado em ${banner.renderSeconds != null ? banner.renderSeconds.toFixed(1) : "?"}s.`}
          {banner.adjustments.length > 0 && (
            <ul className="mt-1 list-disc pl-5 text-xs">
              {banner.adjustments.map((a, i) => (
                <li key={i}>
                  {LOSS_ICON[a.kind]} {formatTimecode(a.start)}
                  {a.kind !== "pan" && ` → ${formatTimecode(a.end)}`}
                  {a.text ? ` "${a.text}"` : ""}: {a.change}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {/* Prévias: original (ao vivo) | renderizada (versão salva) — uma
          embaixo da outra em tela estreita (spec §7). */}
      <Card padding="md" className="grid gap-4 sm:grid-cols-[3fr_2fr]">
        <div className="flex flex-col gap-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold tracking-wide text-text-muted">ORIGINAL — prévia ao vivo</span>
            <Badge variant="warning">Prévia aproximada</Badge>
          </div>
          {!sourceFailed ? (
            <div ref={previewBoxRef} className="relative w-full overflow-hidden rounded-lg bg-black">
              <video
                ref={videoRef}
                src={originalVideoUrl(clip.job_id)}
                controls
                className="block w-full"
                onLoadedMetadata={(e) => {
                  const v = e.currentTarget;
                  if (v.videoWidth && v.videoHeight) setVideoAspect(v.videoWidth / v.videoHeight);
                  v.currentTime = draft.range.start;
                }}
                onTimeUpdate={(e) => setPlayhead(e.currentTarget.currentTime)}
                onSeeked={(e) => setPlayhead(e.currentTarget.currentTime)}
                onError={() => setSourceFailed(true)}
              />
              {/* Moldura 9:16: tracejada = enquadramento automático (só
                  ilustrativa, centralizada), sólida = zoom manual. */}
              <div
                className={cn(
                  "pointer-events-none absolute top-0 bottom-10 border-2 border-warning-marker/80",
                  cx === null && "border-dashed",
                )}
                style={{ left: `${frameLeft * 100}%`, width: `${frameFraction * 100}%` }}
              >
                {cs.enabled && currentLine && framePx > 0 && (
                  <div
                    style={captionOverlayStyle(
                      cs.preset as CaptionStyle,
                      cs.color,
                      cs.font_family as CaptionFontFamily,
                      cs.font_size * (framePx / PREVIEW_WIDTH_PX),
                    )}
                  >
                    {currentLine.text}
                  </div>
                )}
              </div>
            </div>
          ) : (
            <div className="flex aspect-video w-full items-center justify-center rounded-lg bg-bg p-2 text-center text-xs text-text-subtle">
              Prévia do vídeo original indisponível pra esse formato — a timeline continua funcionando.
            </div>
          )}
          {!sourceFailed && !draft.has_streamer_region && (
            <div
              onClick={addPanPointAtPlayhead}
              className={cn(
                "relative h-6 w-full rounded-md border border-dashed border-border-strong bg-surface-hover/50",
                rendering ? "cursor-not-allowed" : "cursor-crosshair",
              )}
              title="Clique na posição horizontal onde focar, no instante atual do vídeo"
            >
              <span className="pointer-events-none absolute inset-0 flex items-center justify-center text-[10px] text-text-subtle">
                clique aqui pra marcar o foco do zoom em {formatTimecode(playhead)}
              </span>
            </div>
          )}
          <span className="text-xs text-text-subtle">▶ {formatTimecode(playhead)}</span>
        </div>

        <div className="flex flex-col items-center gap-2">
          <span className="self-start text-xs font-semibold tracking-wide text-text-muted">
            CORTE RENDERIZADO — versão salva
          </span>
          <div className="relative aspect-[9/16] w-full max-w-[220px] overflow-hidden rounded-lg bg-black">
            <video
              // `key` + query mudam a cada render bem-sucedido: força o
              // navegador a buscar o arquivo novo em vez do cache.
              key={`${clip.revision}-${previewNonce}`}
              src={`${clipFileUrl(clip.id)}?v=${clip.revision}-${previewNonce}`}
              muted
              loop
              autoPlay
              controls
              className="h-full w-full object-cover"
            />
            {rendering && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/60">
                <Spinner size="lg" label="Renderizando" />
              </div>
            )}
          </div>
          {dirty && (
            <Badge variant="warning" className="text-center">
              ⚠ Desatualizado — há mudanças não renderizadas
            </Badge>
          )}
        </div>
      </Card>

      {/* Estilo da legenda (recolhível) — reaproveita o CaptionSection. */}
      <Card padding="sm" className="flex flex-col gap-3">
        <button
          type="button"
          onClick={() => setStyleOpen((o) => !o)}
          aria-expanded={styleOpen}
          className={cn("self-start text-sm font-medium text-text-secondary", FOCUS_VISIBLE_RING)}
        >
          Estilo da legenda {styleOpen ? "▾" : "▸"}
        </button>
        {styleOpen && (
          <fieldset disabled={rendering}>
            <CaptionSection
              captionsEnabled={cs.enabled}
              fontSize={cs.font_size}
              color={cs.color}
              style={cs.preset as CaptionStyle}
              fontFamily={cs.font_family as CaptionFontFamily}
              onCaptionsEnabledChange={(v) => updateDraft({ caption_style: { ...cs, enabled: v } })}
              onFontSizeChange={(v) => updateDraft({ caption_style: { ...cs, font_size: v } })}
              onColorChange={(v) => updateDraft({ caption_style: { ...cs, color: v } })}
              onStyleChange={(v) => updateDraft({ caption_style: { ...cs, preset: v } })}
              onFontFamilyChange={(v) => updateDraft({ caption_style: { ...cs, font_family: v } })}
            />
          </fieldset>
        )}
      </Card>

      <Card padding="md" className="flex flex-col gap-4">
        <ReviewTimeline
          windowStart={windowStart}
          windowEnd={windowEnd}
          range={draft.range}
          captions={draft.captions}
          pan={draft.pan}
          removed={draft.removed}
          profanityRanges={draft.profanity.ranges}
          hasStreamerRegion={draft.has_streamer_region}
          words={snapUnavailable ? [] : words}
          playhead={playhead}
          readOnly={rendering}
          selectedCaptionIndex={selectedCaption}
          onRangeChange={(range) => updateDraft({ range })}
          onRemovedChange={(removed) => updateDraft({ removed })}
          onCaptionClick={(i) => {
            setSelectedCaption(i);
            seek(draft.captions.lines[i].start);
          }}
          onSeek={seek}
        />

        {/* Edição do TEXTO da linha clicada (os tempos não são editáveis na
            v1). Não usa o CaptionTextEditor antigo direto porque ele grava
            na hora pelo PUT /captions (tempos relativos, um render por
            salvar) — aqui o texto só muda o rascunho; mesmo visual dele. */}
        {selectedLine && selectedCaption !== null && (
          <div className="flex flex-col gap-1 rounded bg-surface-hover px-2 py-2">
            <div className="flex items-center justify-between">
              <span className="text-[11px] text-text-subtle">
                Legenda {selectedCaption + 1}: {formatTimecode(selectedLine.start)} → {formatTimecode(selectedLine.end)}
                {draft.captions.mode === "manual" ? " (editada manualmente)" : " (transcrição automática)"}
              </span>
              <button
                type="button"
                onClick={() => setSelectedCaption(null)}
                className={cn("rounded text-text-subtle hover:text-heading", FOCUS_VISIBLE_RING)}
                aria-label="Fechar edição da legenda"
              >
                ✕
              </button>
            </div>
            <textarea
              value={selectedLine.text}
              disabled={rendering}
              onChange={(e) => setCaptionText(selectedCaption, e.target.value)}
              rows={2}
              className={cn(
                "w-full resize-none rounded-md border border-border-strong bg-surface px-2 py-1 text-xs text-text",
                MOTION_SAFE_TRANSITION,
                "outline-none focus:border-accent focus:[--tw-outline-style:solid] focus:outline-2 focus:outline-accent focus:outline-offset-2",
              )}
            />
            {draft.captions.mode === "manual" && (
              <Button
                variant="secondary"
                size="sm"
                className="self-start"
                disabled={rendering}
                onClick={() =>
                  // Volta pro automático. Se a versão salva já era `auto`,
                  // restaura exatamente as falas dela; senão marca `auto` e
                  // o backend recalcula as falas ao renderizar.
                  updateDraft({
                    captions:
                      serverDraft.captions.mode === "auto"
                        ? serverDraft.captions
                        : { mode: "auto", lines: draft.captions.lines },
                  })
                }
              >
                Restaurar transcrição automática
              </Button>
            )}
          </div>
        )}

        {!draft.has_streamer_region && draft.pan.length > 0 && (
          <ul className="flex flex-wrap gap-1 text-xs text-text-secondary">
            {draft.pan.map((p) => (
              <li key={p.t} className="flex items-center gap-2 rounded bg-surface-hover px-2 py-1">
                <span>
                  ◆ {formatTimecode(p.t)} → {(p.cx * 100).toFixed(0)}%
                </span>
                <button
                  type="button"
                  disabled={rendering}
                  onClick={() => updateDraft({ pan: draft.pan.filter((x) => x.t !== p.t) })}
                  className={cn("rounded text-text-subtle hover:text-danger-text-strong", FOCUS_VISIBLE_RING)}
                  aria-label={`Remover ponto de zoom em ${formatTimecode(p.t)}`}
                >
                  ✕
                </button>
              </li>
            ))}
          </ul>
        )}
      </Card>

      {losses && (
        <LossesDialog
          losses={losses}
          onBackToEdit={() => setLosses(null)}
          onConfirm={() => render(true)}
        />
      )}
    </div>
  );
}

/**
 * Modal de perdas (spec §7) — só aparece DEPOIS do Renderizar, quando o
 * backend responde 409 losses_require_confirmation. "Voltar e ajustar" é o
 * padrão (foco inicial, Esc) por ser o lado seguro; "Descartar e renderizar"
 * reenvia o MESMO corpo com confirm_losses: true.
 */
function LossesDialog({
  losses,
  onBackToEdit,
  onConfirm,
}: {
  losses: EditChange[];
  onBackToEdit: () => void;
  onConfirm: () => void;
}) {
  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4"
      role="dialog"
      aria-modal="true"
      aria-labelledby="losses-title"
      onKeyDown={(e) => {
        if (e.key === "Escape") {
          // stopPropagation: o Esc da tela (sair do editor) escuta no
          // document e não pode disparar junto com o fechar do modal.
          e.stopPropagation();
          onBackToEdit();
        }
      }}
    >
      <Card padding="md" className="flex w-full max-w-md flex-col gap-3">
        <p id="losses-title" className="text-sm font-semibold text-heading">
          {losses.length} {losses.length === 1 ? "item ficou" : "itens ficaram"} fora do novo corte e{" "}
          {losses.length === 1 ? "será descartado" : "serão descartados"}:
        </p>
        <ul className="flex max-h-64 flex-col gap-1 overflow-y-auto text-xs text-text-secondary">
          {losses.map((l, i) => (
            <li key={i} className="flex gap-2 rounded bg-surface-hover px-2 py-1">
              <span aria-hidden="true">{LOSS_ICON[l.kind]}</span>
              <span className="tabular-nums">
                {formatTimecode(l.start)}
                {l.kind !== "pan" && ` → ${formatTimecode(l.end)}`}
              </span>
              {l.text && <span className="truncate">"{l.text}"</span>}
            </li>
          ))}
        </ul>
        <div className="flex justify-end gap-2">
          <Button variant="danger" size="sm" onClick={onConfirm}>
            Descartar e renderizar
          </Button>
          <Button size="sm" autoFocus onClick={onBackToEdit}>
            Voltar e ajustar
          </Button>
        </div>
      </Card>
    </div>
  );
}
