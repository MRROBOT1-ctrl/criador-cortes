import { useEffect, useState } from "react";
import { deleteWatermark, getWatermarkStatus, uploadWatermark, watermarkPreviewUrl } from "../api";
import type { WatermarkPosition } from "../types";

const POSITIONS: { value: WatermarkPosition; label: string }[] = [
  { value: "bottom-right", label: "Inferior direita" },
  { value: "bottom-left", label: "Inferior esquerda" },
  { value: "top-right", label: "Superior direita" },
  { value: "top-left", label: "Superior esquerda" },
];

interface Props {
  enabled: boolean;
  position: WatermarkPosition;
  onEnabledChange: (value: boolean) => void;
  onPositionChange: (value: WatermarkPosition) => void;
}

/**
 * Logo/marca d'água — uma imagem só, global pro projeto inteiro (ver
 * app/routers/watermark.py no backend). Aqui só gerencia essa imagem
 * (upload/preview/remoção); o toggle "aplicar neste job" e a posição são
 * controlados pelo UploadForm (fazem parte do UploadConfig do job).
 */
export function WatermarkSection({ enabled, position, onEnabledChange, onPositionChange }: Props) {
  const [hasLogo, setHasLogo] = useState<boolean | null>(null);
  const [previewKey, setPreviewKey] = useState(0);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getWatermarkStatus()
      .then((s) => setHasLogo(s.exists))
      .catch(() => setHasLogo(false));
  }, []);

  async function handleFile(e: React.ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    e.target.value = ""; // permite reenviar o mesmo arquivo depois, se precisar
    if (!file) return;

    setError(null);
    setUploading(true);
    try {
      const status = await uploadWatermark(file);
      setHasLogo(status.exists);
      setPreviewKey((k) => k + 1); // força o <img> a recarregar a nova logo
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao enviar a logo.");
    } finally {
      setUploading(false);
    }
  }

  async function handleRemove() {
    setError(null);
    try {
      const status = await deleteWatermark();
      setHasLogo(status.exists);
      if (enabled) onEnabledChange(false);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao remover a logo.");
    }
  }

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-slate-700 bg-slate-800/50 p-3">
      <div className="flex items-center justify-between gap-3">
        <label className="flex items-center gap-2 text-sm font-medium text-slate-300">
          <input
            type="checkbox"
            checked={enabled}
            disabled={!hasLogo}
            onChange={(e) => onEnabledChange(e.target.checked)}
            className="h-4 w-4 rounded border-slate-600 bg-slate-800 accent-indigo-600"
          />
          Aplicar marca d'água/logo nos clipes
        </label>
        {hasLogo && (
          <img
            key={previewKey}
            src={watermarkPreviewUrl()}
            alt="Prévia da logo"
            className="h-8 w-auto rounded border border-slate-700 bg-slate-900 object-contain"
          />
        )}
      </div>

      {enabled && (
        <div className="flex flex-col gap-1.5">
          <label htmlFor="watermark_position" className="text-xs font-medium text-slate-400">
            Posição
          </label>
          <select
            id="watermark_position"
            value={position}
            onChange={(e) => onPositionChange(e.target.value as WatermarkPosition)}
            className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
          >
            {POSITIONS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </select>
        </div>
      )}

      <div className="flex items-center gap-2">
        <label className="cursor-pointer rounded-md bg-slate-700 px-3 py-1.5 text-xs font-medium text-slate-200 hover:bg-slate-600">
          {uploading ? "Enviando..." : hasLogo ? "Trocar logo" : "Enviar logo (PNG/JPG)"}
          <input
            type="file"
            accept=".png,.jpg,.jpeg,.webp"
            onChange={handleFile}
            disabled={uploading}
            className="hidden"
          />
        </label>
        {hasLogo && (
          <button
            type="button"
            onClick={handleRemove}
            className="rounded-md px-3 py-1.5 text-xs font-medium text-red-400 hover:bg-red-950/50"
          >
            Remover
          </button>
        )}
      </div>

      <p className="text-xs text-slate-500">
        A logo é única pra todos os vídeos processados aqui — troque quando quiser, mas ela vale
        pra qualquer job que marcar a caixa acima.
      </p>

      {error && <p className="text-xs text-red-400">{error}</p>}
    </div>
  );
}
