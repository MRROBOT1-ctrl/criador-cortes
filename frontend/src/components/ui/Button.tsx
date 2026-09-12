import type { ButtonHTMLAttributes, ReactNode } from "react";
import { buttonClasses } from "./buttonStyles";
import type { ButtonSize, ButtonVariant } from "./buttonStyles";
import { Spinner } from "./Spinner";

export type { ButtonSize, ButtonVariant } from "./buttonStyles";

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Mostra um Spinner antes do conteúdo e desabilita o botão — usado nos
   * "Enviando.../Aplicando.../Baixando..." que hoje só trocam o texto. */
  loading?: boolean;
  children?: ReactNode;
}

/**
 * Botão base do design system. `type="button"` por padrão — evita submit
 * acidental dentro de um `<form>` (só quem realmente precisa submeter passa
 * `type="submit"` explícito).
 */
export function Button({
  variant = "primary",
  size = "md",
  loading = false,
  disabled,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      disabled={disabled || loading}
      className={buttonClasses(variant, size, className)}
      {...rest}
    >
      {loading && <Spinner size="sm" />}
      {children}
    </button>
  );
}
