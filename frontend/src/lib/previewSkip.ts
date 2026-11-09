/**
 * Regra de quais trechos a prévia ao vivo pula durante a reprodução.
 *
 * Funções puras, sem React, como `snap.ts`. O vídeo original não tem os cortes
 * internos, então a tela precisa saltar o que não vai existir no corte final.
 */
import type { ProfanityRange, TimeRange } from "../types";

// Sem essa folga, um seek que cai um pouco antes do fim do trecho o faria
// disparar de novo, prendendo o vídeo num salto repetido.
const SKIP_TOLERANCE_SECONDS = 0.01;

/**
 * Trechos que somem do vídeo final, em ordem de início: removidos pelo usuário
 * e cortes do filtro de palavrões. Os trechos mudos continuam na imagem e por
 * isso não entram.
 */
export function previewSkipRanges(removed: TimeRange[], profanity: ProfanityRange[]): TimeRange[] {
  return [...removed, ...profanity.filter((r) => r.kind === "cut")].sort((a, b) => a.start - b.start);
}

/**
 * Próximo instante tocável a partir de `t`, atravessando trechos encostados ou
 * sobrepostos de uma vez. `null` quando `t` não está em nenhum trecho.
 * `ranges` precisa estar ordenado por início.
 */
export function nextPlayableTime(t: number, ranges: TimeRange[]): number | null {
  let cursor = t;
  for (const range of ranges) {
    if (range.start > cursor + SKIP_TOLERANCE_SECONDS) break;
    if (cursor < range.end - SKIP_TOLERANCE_SECONDS) cursor = range.end;
  }
  return cursor === t ? null : cursor;
}
