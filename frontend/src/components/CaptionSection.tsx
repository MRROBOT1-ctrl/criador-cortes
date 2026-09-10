import type { CaptionFontFamily, CaptionStyle } from "../types";

const STYLES: { value: CaptionStyle; label: string }[] = [
  { value: "bold_outline", label: "Negrito sem caixa (padrão viral)" },
  { value: "boxed", label: "Caixa preta clássica" },
  { value: "minimal", label: "Minimalista" },
  { value: "no_outline", label: "Sem contorno (texto flutuando)" },
];

const FONT_FAMILIES: { value: CaptionFontFamily; label: string }[] = [
  { value: "DejaVu Sans", label: "DejaVu Sans (padrão)" },
  { value: "DejaVu Serif", label: "DejaVu Serif" },
  { value: "DejaVu Sans Mono", label: "DejaVu Mono" },
  { value: "Liberation Sans", label: "Liberation Sans (estilo Arial)" },
  { value: "Liberation Serif", label: "Liberation Serif (estilo Times)" },
  { value: "Liberation Mono", label: "Liberation Mono (estilo Courier)" },
];

// Faixa recalibrada: testado visualmente que o FontSize do libass numa
// legenda SRT sem cabeçalho PlayRes escala BEM maior do que o número sugere
// (FontSize=20, o default antigo, já ocupava ~35% da altura do quadro em 4
// linhas — usuário reportou "legenda muito grande"). FontSize=8 é o
// equivalente ao tamanho comum de legenda de corte curto (~2 linhas).
const FONT_SIZE_MIN = 6;
const FONT_SIZE_MAX = 20;

interface Props {
  captionsEnabled: boolean;
  fontSize: number;
  color: string;
  style: CaptionStyle;
  fontFamily: CaptionFontFamily;
  onCaptionsEnabledChange: (value: boolean) => void;
  onFontSizeChange: (value: number) => void;
  onColorChange: (value: string) => void;
  onStyleChange: (value: CaptionStyle) => void;
  onFontFamilyChange: (value: CaptionFontFamily) => void;
}

/**
 * Estilo da legenda queimada nos clipes (ver app/services/video_cutter.py no
 * backend). A posição vertical (MarginV) é fixa por preset e não é exposta
 * aqui de propósito — testado empiricamente que valores altos empurram a
 * legenda pra fora da tela.
 */
export function CaptionSection({
  captionsEnabled,
  fontSize,
  color,
  style,
  fontFamily,
  onCaptionsEnabledChange,
  onFontSizeChange,
  onColorChange,
  onStyleChange,
  onFontFamilyChange,
}: Props) {
  return (
    <div className="flex flex-col gap-3 rounded-lg border border-slate-700 bg-slate-800/50 p-3">
      <label className="flex items-center gap-2 text-sm font-medium text-slate-300">
        <input
          type="checkbox"
          checked={captionsEnabled}
          onChange={(e) => onCaptionsEnabledChange(e.target.checked)}
          className="h-4 w-4 rounded border-slate-600 bg-slate-800 accent-indigo-600"
        />
        Queimar legenda no vídeo
      </label>

      {captionsEnabled && (
        <>
          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="caption_font_size" className="text-xs font-medium text-slate-400">
                Tamanho da fonte ({fontSize}px)
              </label>
              <input
                id="caption_font_size"
                type="range"
                min={FONT_SIZE_MIN}
                max={FONT_SIZE_MAX}
                value={fontSize}
                onChange={(e) => onFontSizeChange(Number(e.target.value))}
                className="accent-indigo-600"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="caption_color" className="text-xs font-medium text-slate-400">
                Cor do texto
              </label>
              <input
                id="caption_color"
                type="color"
                value={color}
                onChange={(e) => onColorChange(e.target.value)}
                className="h-9 w-full cursor-pointer rounded-lg border border-slate-700 bg-slate-800 p-1"
              />
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <label htmlFor="caption_style" className="text-xs font-medium text-slate-400">
                Formato
              </label>
              <select
                id="caption_style"
                value={style}
                onChange={(e) => onStyleChange(e.target.value as CaptionStyle)}
                className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              >
                {STYLES.map((s) => (
                  <option key={s.value} value={s.value}>
                    {s.label}
                  </option>
                ))}
              </select>
            </div>

            <div className="flex flex-col gap-1.5">
              <label htmlFor="caption_font_family" className="text-xs font-medium text-slate-400">
                Fonte
              </label>
              <select
                id="caption_font_family"
                value={fontFamily}
                onChange={(e) => onFontFamilyChange(e.target.value as CaptionFontFamily)}
                className="rounded-lg border border-slate-700 bg-slate-800 px-3 py-2 text-sm text-slate-100"
              >
                {FONT_FAMILIES.map((f) => (
                  <option key={f.value} value={f.value}>
                    {f.label}
                  </option>
                ))}
              </select>
            </div>
          </div>
        </>
      )}
    </div>
  );
}
