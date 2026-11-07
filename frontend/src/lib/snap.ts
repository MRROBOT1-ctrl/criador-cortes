/**
 * Snap das alças do corte em fronteiras de palavra (spec §8).
 *
 * Função pura, sem React: o componente passa o tempo solto, qual borda está
 * sendo arrastada, as palavras e se o Alt está apertado. Fica separada pra
 * dar de testar sem montar a timeline e pra ter uma regra só.
 */

/** Tolerância do snap: além disso a borda fica onde o usuário soltou. */
export const SNAP_TOLERANCE_SECONDS = 0.3;

/** Início do corte encaixa no `start` da palavra; fim, no `end`. */
export type SnapEdge = "start" | "end";

/** Só o que o snap precisa de uma palavra — compatível com `WordOut`. */
export interface SnapWord {
  start: number;
  end: number;
}

export interface SnapOptions {
  /** Alt (Option no Mac) segurado durante o arrasto desliga o snap. */
  altKey?: boolean;
  /** Só pra teste; a regra da v1 é 300 ms. */
  toleranceSeconds?: number;
}

export interface SnapResult {
  time: number;
  /** true se encaixou numa palavra (a tela pode destacar a alça). */
  snapped: boolean;
}

export function snapTime(
  time: number,
  edge: SnapEdge,
  words: readonly SnapWord[],
  options: SnapOptions = {},
): SnapResult {
  const tolerance = options.toleranceSeconds ?? SNAP_TOLERANCE_SECONDS;
  if (options.altKey || words.length === 0) return { time, snapped: false };

  let best: number | null = null;
  let bestDistance = Infinity;
  for (const word of words) {
    const boundary = edge === "start" ? word.start : word.end;
    const distance = Math.abs(boundary - time);
    if (distance < bestDistance) {
      bestDistance = distance;
      best = boundary;
    }
  }
  // `<=`: exatamente 300 ms ainda encaixa ("a mais de 300 ms" é que não).
  // O epsilon absorve erro de ponto flutuante (ex: 1.3 - 1.0 = 0.30000000000000004).
  if (best !== null && bestDistance <= tolerance + 1e-9) {
    return { time: best, snapped: true };
  }
  return { time, snapped: false };
}
