import { useEffect, useRef, useState } from "react";
import { clipFileUrl, metricsUiUrl } from "../api";
import type { ClipOut, Platform } from "../types";
import { Badge, Button, Card, buttonClasses, cn } from "./ui";

const PLATFORM_LABELS: Record<Platform, string> = {
  tiktok: "TikTok",
  instagram_reels: "Instagram Reels",
  youtube_shorts: "YouTube Shorts",
  facebook_reels: "Facebook Reels",
};

interface Props {
  clip: ClipOut;
  onEdit: (clip: ClipOut) => void;
  /** Índice na lista — usado só pra escalonar o fade-in de entrada (ver
   * ClipsList), sem precisar de um wrapper <div> extra em volta do Card. */
  index?: number;
}

export function ClipCard({ clip, onEdit, index = 0 }: Props) {
  const [copiedPlatform, setCopiedPlatform] = useState<string | null>(null);
  const [copiedTitle, setCopiedTitle] = useState<string | null>(null);
  // Só muda quando um botão "Copiar" é de fato clicado (não a cada
  // re-render) — usado como `key` do toast "Copiado!" abaixo, pra reiniciar
  // a animação de fade mesmo clicando duas vezes seguidas na mesma linha
  // (sem isso, Date.now() calculado direto no render mudaria toda hora à
  // toa, e um valor fixo nunca remontaria de novo pro mesmo título/plataforma).
  const [copyNonce, setCopyNonce] = useState(0);
  // Muda toda vez que o clipe volta atualizado da tela de edição (agora uma
  // tela separada, não mais um modal aqui dentro) — força o <video> a
  // recarregar o arquivo (mesma URL, conteúdo novo, o navegador cacheia por
  // padrão). `clip` não tem um campo de versão/timestamp próprio, então
  // detecta a mudança comparando a REFERÊNCIA do objeto (App.tsx sempre cria
  // um objeto novo pro clipe editado) — pula a primeira montagem pra não
  // recarregar o vídeo à toa assim que o card aparece.
  const [videoVersion, setVideoVersion] = useState(0);
  const mountedRef = useRef(false);
  useEffect(() => {
    if (mountedRef.current) {
      setVideoVersion((v) => v + 1);
    }
    mountedRef.current = true;
  }, [clip]);

  async function copyHashtags(platform: string, tags: string[]) {
    try {
      await navigator.clipboard.writeText(tags.join(" "));
      // Só reinicia a animação (remontando via `key`) quando o toast tava
      // escondido — clicar 2x rápido em "Copiar" enquanto o "Copiado!"
      // anterior ainda tá visível NÃO remonta, senão o span some (opacity
      // volta a 0) e refaz o fade-in do zero no meio do platô opaco, um
      // flicker perceptível (visto testando com cliques rápidos de verdade).
      if (copiedPlatform !== platform) setCopyNonce((n) => n + 1);
      setCopiedPlatform(platform);
      setTimeout(() => setCopiedPlatform((current) => (current === platform ? null : current)), 1500);
    } catch {
      // clipboard indisponível (ex: contexto não seguro) — ignora silenciosamente
    }
  }

  async function copyTitle(title: string) {
    try {
      await navigator.clipboard.writeText(title);
      // Mesmo raciocínio de copyHashtags acima.
      if (copiedTitle !== title) setCopyNonce((n) => n + 1);
      setCopiedTitle(title);
      setTimeout(() => setCopiedTitle((current) => (current === title ? null : current)), 1500);
    } catch {
      // clipboard indisponível (ex: contexto não seguro) — ignora silenciosamente
    }
  }

  const hashtagEntries = Object.entries(clip.hashtags ?? {}) as [Platform, string[]][];
  const videoSrc = `${clipFileUrl(clip.id)}?v=${videoVersion}`;

  return (
    <Card
      padding="sm"
      className="flex flex-col gap-4 animate-fade-in"
      style={{ animationDelay: `${Math.min(index, 8) * 60}ms` }}
    >
      <video
        key={videoVersion}
        src={videoSrc}
        controls
        className="mx-auto aspect-[9/16] w-full max-w-56 rounded-lg bg-black"
      />

      <div className="flex flex-wrap items-center justify-between gap-2 text-sm text-text-secondary">
        <span>
          Corte #{clip.index + 1}: {clip.start_time.toFixed(1)}s → {clip.end_time.toFixed(1)}s
        </span>
        <Badge>score {clip.score.toFixed(0)}</Badge>
      </div>

      {clip.transcript_text && (
        <p className="max-h-24 overflow-auto rounded-lg bg-bg p-2 text-xs text-text-muted">
          {clip.transcript_text}
        </p>
      )}

      {clip.suggested_titles && clip.suggested_titles.length > 0 && (
        <div className="flex flex-col gap-1 rounded-lg bg-bg p-2">
          <span className="text-xs font-semibold text-text-secondary">Títulos sugeridos</span>
          {clip.suggested_titles.map((title) => (
            <div key={title} className="flex items-center justify-between gap-2">
              <p className="text-xs text-text-muted">{title}</p>
              <Button variant="ghost" size="sm" className="shrink-0" onClick={() => copyTitle(title)}>
                {copiedTitle === title ? (
                  <span key={copyNonce} className="animate-toast">
                    Copiado!
                  </span>
                ) : (
                  "Copiar"
                )}
              </Button>
            </div>
          ))}
        </div>
      )}

      {hashtagEntries.length > 0 && (
        <div className="flex flex-col gap-2">
          {hashtagEntries.map(([platform, tags]) => (
            <div key={platform} className="flex flex-col gap-1 rounded-lg bg-bg p-2">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-text-secondary">
                  {PLATFORM_LABELS[platform] ?? platform}
                </span>
                <Button variant="ghost" size="sm" onClick={() => copyHashtags(platform, tags)}>
                  {copiedPlatform === platform ? (
                    <span key={copyNonce} className="animate-toast">
                      Copiado!
                    </span>
                  ) : (
                    "Copiar"
                  )}
                </Button>
              </div>
              <p className="text-xs text-text-subtle">{tags.join(" ")}</p>
            </div>
          ))}
        </div>
      )}

      <div className="flex flex-wrap gap-2">
        {/* Editar era bg-surface-active (um tom mais escuro que "secondary")
            — consolidado aqui pro mesmo variant="secondary" das outras duas
            (Ver métricas já era exatamente isso); diferença mínima, ver
            relatório da Parte 2. */}
        <Button variant="secondary" size="sm" className="flex-1" onClick={() => onEdit(clip)}>
          ✂️ Editar
        </Button>
        {/* <a> em vez de Button: precisa do atributo `download`, que um
            <button> não tem — reaproveita as mesmas classes via buttonClasses. */}
        <a href={clipFileUrl(clip.id)} download className={cn("flex-1", buttonClasses("primary", "sm"))}>
          Baixar clipe
        </a>
        <a
          href={metricsUiUrl(clip.id)}
          target="_blank"
          rel="noreferrer"
          className={cn("flex-1", buttonClasses("secondary", "sm"))}
        >
          Ver métricas
        </a>
      </div>
    </Card>
  );
}
