/**
 * Estados padronizados (Parte 3, item 3a) reaproveitados por Button, Input,
 * Select e Card — antes cada elemento interativo tinha hover/focus/transition
 * inconsistentes (alguns com transition, outros sem; foco nunca customizado,
 * só o outline default do navegador). Ver buttonStyles.ts pra explicação
 * completa de cada decisão (outline em vez de ring, motion-safe, etc.).
 */

// IMPORTANTE — bug real encontrado testando o computed style em produção
// (não só olhando o CSS gerado): `outline-none` seta a custom property
// `--tw-outline-style` pra "none" numa classe SEM pseudo-classe nenhuma, ou
// seja, ela vale SEMPRE nesse elemento. `outline-2`/etc (inclusive as
// variantes focus-visible/focus) leem o ESTILO do outline de
// `outline-style: var(--tw-outline-style)` — nunca escrevem "solid" direto.
// Resultado: mesmo com :focus-visible batendo, a variável continuava "none"
// (só `outline-none` a define), então outline-color/outline-width ficavam
// certos mas o outline NUNCA aparecia (outline-style:none esconde tudo).
// A correção é reescrever a variável TAMBÉM dentro do estado de foco, com
// especificidade maior (a regra com :focus-visible/:focus tem 1 seletor a
// mais que a de `outline-none` sozinha, então vence) — via utility
// arbitrária do Tailwind (`[--tw-outline-style:solid]`), já que não existe
// uma classe pronta "outline-style-solid".

// Pra botões/cards clicáveis: só mostra o anel de foco em navegação por
// teclado (:focus-visible) — um clique de mouse não deixa "preso" na tela.
export const FOCUS_VISIBLE_RING =
  "outline-none focus-visible:[--tw-outline-style:solid] focus-visible:outline-2 " +
  "focus-visible:outline-accent focus-visible:outline-offset-2";

// Pra campos de formulário: mostra em QUALQUER foco (:focus), inclusive
// clique de mouse — é o comportamento esperado num input/select.
export const FOCUS_RING =
  "outline-none focus:[--tw-outline-style:solid] focus:outline-2 focus:outline-accent focus:outline-offset-2";

// `motion-safe:` faz a transição em si sumir com prefers-reduced-motion
// (a mudança de estado continua acontecendo, só sem a suavização).
export const MOTION_SAFE_TRANSITION = "motion-safe:transition motion-safe:duration-150 motion-safe:ease-out";
