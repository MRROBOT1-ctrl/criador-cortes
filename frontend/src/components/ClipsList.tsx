import { downloadAllUrl } from "../api";
import type { ClipOut } from "../types";
import { ClipCard } from "./ClipCard";

interface Props {
  jobId: string;
  clips: ClipOut[];
  onClipUpdated: (clip: ClipOut) => void;
  onReset: () => void;
}

export function ClipsList({ jobId, clips, onClipUpdated, onReset }: Props) {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-semibold text-slate-100">
          {clips.length} corte{clips.length === 1 ? "" : "s"} gerado{clips.length === 1 ? "" : "s"}
        </h2>
        <div className="flex gap-2">
          <a
            href={downloadAllUrl(jobId)}
            className="rounded-lg bg-indigo-600 px-4 py-2 text-sm font-semibold text-white hover:bg-indigo-500"
          >
            Baixar tudo (ZIP)
          </a>
          <button
            type="button"
            onClick={onReset}
            className="rounded-lg bg-slate-800 px-4 py-2 text-sm font-semibold text-slate-200 hover:bg-slate-700"
          >
            Novo vídeo
          </button>
        </div>
      </div>

      {clips.length === 0 ? (
        <p className="text-sm text-slate-400">Nenhum clipe foi gerado para este vídeo.</p>
      ) : (
        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {clips.map((clip) => (
            <ClipCard key={clip.id} clip={clip} onUpdated={onClipUpdated} />
          ))}
        </div>
      )}
    </div>
  );
}
