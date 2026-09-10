import { useEffect, useState } from "react";
import { getClipCaptions, setClipCaptions } from "../api";
import type { CaptionLine, ClipOut } from "../types";

function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return `${Math.floor(s / 60)}:${(s % 60).toString().padStart(2, "0")}`;
}

interface Props {
  clip: ClipOut;
  onApplied: (clip: ClipOut) => void;
}

/**
 * Correção manual do TEXTO da legenda queimada — o tempo de cada linha vem
 * da transcrição do Whisper (ou do override já salvo) e não é editável
 * aqui, só a palavra/frase em si (ver app/routers/captions.py). Salvar
 * re-renderiza o clipe com o texto corrigido; "Restaurar transcrição
 * original" remove o override e volta pra legenda automática.
 */
export function CaptionTextEditor({ clip, onApplied }: Props) {
  const [lines, setLines] = useState<CaptionLine[]>([]);
  const [isManual, setIsManual] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function reload(showLoading: boolean): Promise<void> {
    if (showLoading) setLoading(true);
    setError(null);
    return getClipCaptions(clip.id)
      .then((res) => {
        setLines(res.lines);
        setIsManual(res.is_manual);
      })
      .catch((err) => {
        setError(err instanceof Error ? err.message : "Falha ao carregar a legenda.");
      })
      .finally(() => {
        if (showLoading) setLoading(false);
      });
  }

  useEffect(() => {
    reload(true);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [clip.id]);

  function updateText(index: number, text: string) {
    setLines((prev) => prev.map((line, i) => (i === index ? { ...line, text } : line)));
  }

  async function handleSave() {
    setError(null);
    setSaving(true);
    try {
      const updated = await setClipCaptions(clip.id, lines);
      setIsManual(true);
      onApplied(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao salvar a legenda.");
    } finally {
      setSaving(false);
    }
  }

  async function handleRestore() {
    setError(null);
    setSaving(true);
    try {
      const updated = await setClipCaptions(clip.id, []);
      onApplied(updated);
      await reload(false); // busca de volta as linhas derivadas do Whisper pra mostrar na lista
    } catch (err) {
      setError(err instanceof Error ? err.message : "Falha ao restaurar a transcrição original.");
    } finally {
      setSaving(false);
    }
  }

  if (loading) {
    return <p className="text-xs text-slate-500">Carregando legenda...</p>;
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-slate-500">
        Corrija o texto que o Whisper transcreveu errado (o tempo de cada linha não muda).
        {isManual ? " (legenda editada manualmente)" : " (usando transcrição automática)"}
      </p>

      {lines.length === 0 && (
        <p className="text-xs text-slate-500">Nenhuma linha de legenda encontrada para esse trecho.</p>
      )}

      {lines.length > 0 && (
        <ul className="flex flex-col gap-2">
          {lines.map((line, i) => (
            <li key={`${line.start}-${i}`} className="flex flex-col gap-1 rounded bg-slate-800 px-2 py-2">
              <span className="text-[11px] text-slate-500">
                {formatTime(line.start)} → {formatTime(line.end)}
              </span>
              <textarea
                value={line.text}
                onChange={(e) => updateText(i, e.target.value)}
                rows={2}
                className="w-full resize-none rounded-md border border-slate-700 bg-slate-900 px-2 py-1 text-xs text-slate-100"
              />
            </li>
          ))}
        </ul>
      )}

      {error && <p className="text-xs text-red-400">{error}</p>}

      <div className="flex gap-2">
        <button
          type="button"
          disabled={saving || lines.length === 0}
          onClick={handleSave}
          className="rounded-md bg-indigo-600 px-3 py-1 text-xs font-semibold text-white hover:bg-indigo-500 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {saving ? "Salvando..." : "Salvar legendas"}
        </button>
        {isManual && (
          <button
            type="button"
            disabled={saving}
            onClick={handleRestore}
            className="rounded-md bg-slate-800 px-3 py-1 text-xs font-semibold text-slate-200 hover:bg-slate-700"
          >
            Restaurar transcrição original
          </button>
        )}
      </div>
    </div>
  );
}
