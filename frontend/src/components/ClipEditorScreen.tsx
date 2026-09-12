import { useEffect, useRef, useState } from "react";
import { clipFileUrl, originalVideoUrl, recutClip } from "../api";
import type { CaptionFontFamily, CaptionStyle, ClipOut } from "../types";
import { CaptionSection } from "./CaptionSection";
import { CaptionTextEditor } from "./CaptionTextEditor";
import { CutSegmentsEditor } from "./CutSegmentsEditor";
import { ManualPanEditor } from "./ManualPanEditor";
import { TimelineScrubber } from "./TimelineScrubber";
import { Button, Card, cn, FOCUS_VISIBLE_RING, MOTION_SAFE_TRANSITION } from "./ui";

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
  useEffect(() => {
    setStart(clip.start_time);
    setEnd(clip.end_time);
    setTab("corte");
    initialFormRef.current = {
      start: clip.start_time,
      end: clip.end_time,
      captionsEnabled,
      captionFontSize,
      captionColor,
      captionStyle,
      captionFontFamily,
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.id]);

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
    onBack();
  }

  useEffect(() => {
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") requestBack();
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isDirty, onBack]);

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
            onClick={() => setTab(t.id)}
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

      <Card padding="md">
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
