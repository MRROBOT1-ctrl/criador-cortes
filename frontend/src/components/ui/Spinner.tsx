import { cn } from "./cn";

export type SpinnerSize = "sm" | "md" | "lg";

const SIZE_CLASSES: Record<SpinnerSize, string> = {
  sm: "h-3.5 w-3.5 border-2",
  md: "h-5 w-5 border-2",
  lg: "h-8 w-8 border-[3px]",
};

interface SpinnerProps {
  size?: SpinnerSize;
  className?: string;
  label?: string;
}

/**
 * Indicador de carregamento — usado em qualquer estado "Enviando.../
 * Aplicando.../Baixando..." do app (ver `Button` com `loading`). Só CSS
 * (animate-spin do Tailwind), sem lib nova.
 *
 * `role="status"` + `aria-label`: um leitor de tela anuncia que algo está
 * em andamento mesmo sem depender só do texto visível ao lado.
 */
export function Spinner({ size = "md", className, label = "Carregando" }: SpinnerProps) {
  return (
    <span
      role="status"
      aria-label={label}
      className={cn(
        "inline-block shrink-0 animate-spin rounded-full border-border-strong border-t-accent",
        SIZE_CLASSES[size],
        className,
      )}
    />
  );
}
