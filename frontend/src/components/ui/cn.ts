/**
 * Junta classes Tailwind condicionalmente, ignorando falsy — substitui o que
 * uma lib tipo `clsx` faria, sem precisar adicionar dependência nova só pra
 * isso (ver regra da Parte 2: nenhuma lib nova sem perguntar antes).
 */
export function cn(...classes: Array<string | false | null | undefined>): string {
  return classes.filter(Boolean).join(" ");
}
