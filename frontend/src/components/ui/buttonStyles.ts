import { cn } from "./cn";
import { FOCUS_VISIBLE_RING, MOTION_SAFE_TRANSITION } from "./interactiveStates";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

const TEXT_SIZE: Record<ButtonSize, string> = {
  sm: "text-xs",
  md: "text-sm",
  lg: "text-base",
};

// Só as variantes com fundo/borda (padded=true) usam essa faixa — "ghost"
// hoje é sempre texto solto (link-style), sem caixa ao redor.
const PADDING_SIZE: Record<ButtonSize, string> = {
  sm: "rounded-md px-3 py-1",
  md: "rounded-lg px-4 py-2",
  lg: "rounded-lg px-6 py-3",
};

// Extraído 1:1 do que já existia espalhado pelo projeto (ver auditoria antes
// de mexer): cada variante replica exatamente a combinação de cor/peso de
// fonte/padding já em uso, só que num lugar só agora.
const VARIANT_STYLES: Record<ButtonVariant, { classes: string; weight: string; padded: boolean }> = {
  primary: {
    classes: "bg-accent text-white hover:bg-accent-hover",
    weight: "font-semibold",
    padded: true,
  },
  secondary: {
    classes: "bg-surface-hover text-heading hover:bg-surface-active",
    weight: "font-semibold",
    padded: true,
  },
  // Sem fundo/padding — é o estilo "link" usado em "← Voltar", "Copiar" etc.
  ghost: {
    classes: "text-accent-text hover:text-accent-text-hover",
    weight: "font-medium",
    padded: false,
  },
  danger: {
    classes: "text-danger-text-strong hover:bg-danger-bg/50",
    weight: "font-medium",
    padded: true,
  },
};

// Estados padronizados (Parte 3, item 3a) — os MESMOS pra qualquer variante/
// tamanho, é isso que resolve o "Gerar cortes" ter perdido o transition na
// Parte 2 (só ele tinha a classe escrita à mão) e todo o resto nunca ter tido.
// FOCUS_VISIBLE_RING/MOTION_SAFE_TRANSITION vêm de interactiveStates.ts
// (compartilhado com Input/Select/Card — ver esse arquivo pra explicação
// completa de cada decisão: outline em vez de ring, motion-safe, etc.).
//
// `active:brightness-90`: sempre ativo (não depende de motion) — dá um
// feedback mesmo com reduced-motion. `motion-safe:active:scale-[0.98]` soma
// um leve "afundar" quando animação é permitida.
const INTERACTIVE_STATES = cn(MOTION_SAFE_TRANSITION, FOCUS_VISIBLE_RING, "active:brightness-90 motion-safe:active:scale-[0.98]");

/**
 * Classes visuais de um "botão" do design system, separadas do componente
 * `Button` em si (num arquivo à parte pra não misturar export de componente
 * com export de função no mesmo arquivo — quebra o Fast Refresh, achado
 * pelo oxlint) — reaproveitadas em `<a>` estilizados como botão (ex:
 * ClipCard "Baixar clipe", que precisa do atributo `download`, algo que um
 * `<button>` não tem). Polimorfismo via prop `as` não foi pedido pro Button
 * nessa parte (só pro Card) — isso resolve o mesmo problema sem precisar
 * de union type discriminada.
 */
export function buttonClasses(variant: ButtonVariant = "primary", size: ButtonSize = "md", className?: string): string {
  const v = VARIANT_STYLES[variant];
  return cn(
    "inline-flex items-center justify-center gap-2 text-center disabled:cursor-not-allowed disabled:opacity-50",
    INTERACTIVE_STATES,
    TEXT_SIZE[size],
    v.weight,
    v.padded && PADDING_SIZE[size],
    v.classes,
    className,
  );
}
