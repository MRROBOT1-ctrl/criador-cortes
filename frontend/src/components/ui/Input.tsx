import type { InputHTMLAttributes } from "react";
import { cn } from "./cn";
import { FOCUS_RING, MOTION_SAFE_TRANSITION } from "./interactiveStates";

export type InputVariant = "text" | "number" | "url" | "file" | "color";

// FOCUS_RING usa `:focus` puro (não só `:focus-visible`, ver
// interactiveStates.ts) — diferente de botão, um campo de texto deve
// mostrar o foco em QUALQUER jeito de focar (clique de mouse incluso).
// `focus:border-accent` soma uma mudança de cor na borda, além do anel.
const FOCUS_STATES = cn(MOTION_SAFE_TRANSITION, FOCUS_RING, "focus:border-accent");

const TEXT_LIKE_CLASSES = cn(
  "rounded-lg border border-border-strong bg-surface-hover px-3 py-2 text-sm text-text placeholder:text-text-subtle",
  FOCUS_STATES,
);

const VARIANT_CLASSES: Record<InputVariant, string> = {
  text: TEXT_LIKE_CLASSES,
  number: TEXT_LIKE_CLASSES,
  url: TEXT_LIKE_CLASSES,
  color: cn("h-9 w-full cursor-pointer rounded-lg border border-border-strong bg-surface-hover p-1", FOCUS_STATES),
  file: cn(
    "block w-full cursor-pointer rounded-lg border border-border-strong bg-surface-hover text-sm text-text-secondary",
    "file:mr-4 file:cursor-pointer file:rounded-md file:border-0 file:bg-accent file:px-4 file:py-2",
    "file:text-sm file:font-medium file:text-white motion-safe:file:transition motion-safe:file:duration-150 hover:file:bg-accent-hover",
    FOCUS_STATES,
  ),
};

export interface InputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, "type"> {
  variant?: InputVariant;
  /** Label acima do campo (estilo "form principal" — ver UploadForm). Campos
   * com label menor/inline (ex: CaptionSection) continuam com o próprio
   * `<label>` ao lado, sem usar essa prop — são um estilo visual diferente,
   * não vale forçar os dois no mesmo componente. */
  label?: string;
  error?: string;
  className?: string;
}

export function Input({ variant = "text", label, error, id, className, ...rest }: InputProps) {
  const inputEl = <input id={id} type={variant} className={cn(VARIANT_CLASSES[variant], className)} {...rest} />;

  if (!label && !error) return inputEl;

  return (
    <div className="flex flex-col gap-1.5">
      {label && (
        <label htmlFor={id} className="text-sm font-medium text-text-secondary">
          {label}
        </label>
      )}
      {inputEl}
      {error && <p className="text-xs text-danger-text-strong">{error}</p>}
    </div>
  );
}
