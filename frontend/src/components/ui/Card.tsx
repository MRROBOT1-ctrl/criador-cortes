import type {
  AnchorHTMLAttributes,
  ButtonHTMLAttributes,
  ElementType,
  FormHTMLAttributes,
  HTMLAttributes,
  ReactNode,
} from "react";
import { cn } from "./cn";
import { FOCUS_VISIBLE_RING, MOTION_SAFE_TRANSITION } from "./interactiveStates";

export type CardPadding = "sm" | "md" | "lg";

// Cobre os 3 valores de padding já usados hoje pelos containers (p-4/p-5/p-6).
const PADDING_CLASSES: Record<CardPadding, string> = {
  sm: "p-4",
  md: "p-5",
  lg: "p-6",
};

interface CardOwnProps {
  padding?: CardPadding;
  className?: string;
  children?: ReactNode;
}

// "form" não estava na lista original do spec (div | button | a), mas o
// UploadForm inteiro já era literalmente um card com <form> na raiz — sem
// isso ele teria que ficar de fora do Card ou duplicar a classe visual à
// parte, então estendi (mesmo espírito da Parte 1: fidelidade > lista
// literal quando um caso de uso real pede).
type CardAsProp = "div" | "button" | "a" | "form";

// Props específicas do elemento raiz escolhido — `as="button"` ganha
// onClick/disabled tipados certos, `as="a"` ganha href/target/download etc.
// (a HomeScreen já usa um card clicável como <button>, ver ClipCard/HomeScreen).
type CardProps<T extends CardAsProp> = CardOwnProps & {
  as?: T;
} & (T extends "button"
    ? ButtonHTMLAttributes<HTMLButtonElement>
    : T extends "a"
      ? AnchorHTMLAttributes<HTMLAnchorElement>
      : T extends "form"
        ? FormHTMLAttributes<HTMLFormElement>
        : HTMLAttributes<HTMLDivElement>);

/**
 * Container base (borda + fundo + sombra) usado em praticamente toda tela
 * do app. `as` escolhe o elemento raiz — `"div"` por padrão, `"button"` pra
 * um card clicável com semântica correta (em vez de um `<div onClick>`).
 */
export function Card<T extends CardAsProp = "div">({
  as,
  padding = "lg",
  className,
  children,
  ...rest
}: CardProps<T>) {
  const Component = (as ?? "div") as ElementType;
  // FOCUS_VISIBLE_RING/MOTION_SAFE_TRANSITION são inofensivos num "div"/
  // "form" comum (:focus-visible nunca casa em elemento não focável) — só
  // fazem diferença de verdade quando as="button"/"a". O `active:scale`
  // (leve "afundar" ao clicar) e o `hover:shadow-xl` (Parte 4, hierarquia
  // visual — reforça que o card é clicável) só entram nesses dois casos,
  // de propósito: um Card usado só como container (a maioria) não deveria
  // parecer clicável por engano.
  const isInteractive = as === "button" || as === "a";
  return (
    <Component
      className={cn(
        "rounded-xl border border-border bg-surface shadow-card",
        PADDING_CLASSES[padding],
        MOTION_SAFE_TRANSITION,
        FOCUS_VISIBLE_RING,
        isInteractive && "motion-safe:active:scale-[0.99] hover:shadow-xl",
        className,
      )}
      {...rest}
    >
      {children}
    </Component>
  );
}
