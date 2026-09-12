import { downloadAllUrl } from "../api";
import type { ClipOut } from "../types";
import { ClipCard } from "./ClipCard";
import { JobTimeline } from "./JobTimeline";
import { Button, buttonClasses, EmptyState } from "./ui";

interface Props {
  jobId: string;
  clips: ClipOut[];
  onEditClip: (clip: ClipOut) => void;
  onClipsReordered: (clips: ClipOut[]) => void;
  onReset: () => void;
}

export function ClipsList({ jobId, clips, onEditClip, onClipsReordered, onReset }: Props) {
  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-5">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-xl font-semibold text-text">
          {clips.length} corte{clips.length === 1 ? "" : "s"} gerado{clips.length === 1 ? "" : "s"}
        </h2>
        <div className="flex gap-2">
          <a href={downloadAllUrl(jobId)} className={buttonClasses("primary", "md")}>
            Baixar tudo (ZIP)
          </a>
          <Button variant="secondary" onClick={onReset}>
            Novo vídeo
          </Button>
        </div>
      </div>

      {clips.length === 0 ? (
        <EmptyState
          icon="📭"
          title="Nenhum clipe foi gerado para este vídeo"
          description="O processamento terminou mas não achou nenhum trecho pra cortar — tente com outro vídeo."
          action={
            <Button variant="secondary" onClick={onReset}>
              Novo vídeo
            </Button>
          }
        />
      ) : (
        <>
          {clips.length > 1 && (
            <JobTimeline jobId={jobId} clips={clips} onReordered={onClipsReordered} />
          )}
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {clips.map((clip, index) => (
              <ClipCard key={clip.id} clip={clip} onEdit={onEditClip} index={index} />
            ))}
          </div>
        </>
      )}
    </div>
  );
}
