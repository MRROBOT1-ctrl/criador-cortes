import type { ReactNode } from "react";
import { cn } from "./cn";

export type BadgeVariant = "neutral" | "accent" | "success" | "danger" | "warning";

// Extraído do STATUS_STYLES que estava duplicado em HomeScreen.tsx e do
// StatusBadge que estava duplicado em ProgressPanel.tsx (o mesmo mapa de
// cor-por-status, copiado em dois lugares).
const VARIANT_CLASSES: Record<BadgeVariant, string> = {
  neutral: "bg-surface-hover text-text-secondary",
  accent: "bg-accent-subtle-bg text-accent-subtle-text",
  success: "bg-success-subtle-bg text-success-subtle-text",
  danger: "bg-danger-subtle-bg text-danger-subtle-text",
  warning: "bg-warning/20 text-warning-hover",
};

interface BadgeProps {
  variant?: BadgeVariant;
  className?: string;
  children?: ReactNode;
}

export function Badge({ variant = "neutral", className, children }: BadgeProps) {
  return (
    <span
      // py-1 em vez de py-0.5 (Parte 4, "prominência do badge de status") —
      // o status é a informação mais importante do card/painel, merece um
      // pouco mais de presença que o padding mínimo original.
      className={cn(
        "inline-flex shrink-0 items-center rounded-full px-2.5 py-1 text-xs font-semibold",
        VARIANT_CLASSES[variant],
        className,
      )}
    >
      {children}
    </span>
  );
}
