import { useEffect, useRef, useState } from "react";
import { clipFileUrl, metricsUiUrl } from "../api";
import type { ClipOut, Platform } from "../types";

const PLATFORM_LABELS: Record<Platform, string> = {
  tiktok: "TikTok",
  instagram_reels: "Instagram Reels",
  youtube_shorts: "YouTube Shorts",
  facebook_reels: "Facebook Reels",
};

interface Props {
  clip: ClipOut;
  onEdit: (clip: ClipOut) => void;
}

export function ClipCard({ clip, onEdit }: Props) {
  const [copiedPlatform, setCopiedPlatform] = useState<string | null>(null);
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
      setCopiedPlatform(platform);
      setTimeout(() => setCopiedPlatform((current) => (current === platform ? null : current)), 1500);
    } catch {
      // clipboard indisponível (ex: contexto não seguro) — ignora silenciosamente
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

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => onEdit(clip)}
          className="flex-1 rounded-lg bg-slate-700 px-3 py-2 text-center text-xs font-semibold text-slate-100 hover:bg-slate-600"
        >
          ✂️ Editar
        </button>
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
