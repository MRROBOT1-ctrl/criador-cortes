import type { ReactNode } from "react";

interface Props {
  /** Emoji — mesmo estilo de ícone já usado no resto do app (✂️, ✕ etc.),
   * sem depender de uma lib de ícones nova. */
  icon: string;
  title: string;
  description?: string;
  action?: ReactNode;
}

/**
 * Estado vazio padronizado (Parte 4) — ícone + mensagem amigável + CTA, em
 * vez de um `<p>` cinza isolado. Usado quando a HomeScreen não tem nenhum
 * job ainda, e quando um job terminou sem gerar nenhum clipe.
 */
export function EmptyState({ icon, title, description, action }: Props) {
  return (
    <div className="flex flex-col items-center gap-3 rounded-xl border border-dashed border-border px-6 py-12 text-center">
      <span className="text-4xl" aria-hidden="true">
        {icon}
      </span>
      <p className="text-sm font-medium text-text-secondary">{title}</p>
      {description && <p className="max-w-sm text-xs text-text-subtle">{description}</p>}
      {action}
    </div>
  );
}
