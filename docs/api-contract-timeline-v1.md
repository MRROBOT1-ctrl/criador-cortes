# Contrato da API: Timeline de Revisão v1

> Complementa [`spec-timeline-v1.md`](spec-timeline-v1.md), que é a fonte de
> verdade das **regras**. Este documento fixa **nomes de campos, tipos,
> limites, erros e exemplos**. Se os dois discordarem, pare e avise.
>
> **Regra de coordenação:** qualquer mudança de nome aqui precisa ser avisada
> **antes** do commit, porque o frontend (Agente C) trabalha contra estes
> nomes em paralelo.

**Índice**

| # | Endpoint | Situação |
|---|---|---|
| 1 | `PUT /clips/{clip_id}/edit` (`?dry_run=true`) | novo |
| 2 | `GET /clips/{clip_id}/draft` | novo |
| 3 | `GET /jobs/{job_id}/words` | novo |
| 4 | `POST /clips/{clip_id}/recut` | alterado |
| 5 | `PUT /clips/{clip_id}/pan` | alterado |
| 6 | `PUT /clips/{clip_id}/captions` | alterado |
| 7 | `PUT /clips/{clip_id}/cuts` | alterado |
| 8 | `GET /clips/{clip_id}/metrics` | **não muda** |

---

## 0. Convenções comuns

### 0.1 Tempos
- `number`, em segundos **absolutos no vídeo original** nos endpoints novos
  (1, 2 e 3).
- Os endpoints antigos (4 a 7) **continuam** usando tempos relativos ao
  início do clipe, como hoje. Não mudam de formato.
- Toda resposta arredonda os tempos para **no máximo 3 casas decimais**
  (`round(x, 3)`). Na entrada, qualquer `float` é aceito.

### 0.2 Envelope de erro
Todo erro dos endpoints novos, e o `409 render_in_progress` dos antigos, usa:
```json
{ "detail": { "code": "invalid_range", "message": "O fim do corte precisa ser maior que o início." } }
```
- `code`: `string` estável, para o frontend decidir o que fazer.
- `message`: `string` em PT, pronta para mostrar ao usuário.
- Alguns códigos levam campos extras dentro de `detail`, indicados em cada
  tabela.
- O `422` é o padrão do FastAPI (`{"detail": [ {loc, msg, type}, … ]}`) e
  **não** segue este envelope.
- Os erros que os endpoints antigos já tinham **continuam** como hoje
  (`{"detail": "texto"}`), para não quebrar a tela antiga.

### 0.3 Objeto de estilo de legenda (`ClipCaptionStyle`)
O mesmo objeto aparece no `ClipOut`, no `/draft` e no `/edit`.
```json
{ "enabled": true, "font_size": 8, "color": "#FFFFFF",
  "preset": "bold_outline", "font_family": "DejaVu Sans" }
```
| Campo | Tipo | Validação | Coluna no Clip |
|---|---|---|---|
| `enabled` | `boolean` | | `caption_style_enabled` |
| `font_size` | `integer` | `CAPTION_FONT_SIZE_MIN` ≤ x ≤ `CAPTION_FONT_SIZE_MAX` (`routers/upload.py`) | `caption_style_font_size` |
| `color` | `string` | `validate_caption_color` (`#RRGGBB`) | `caption_style_color` |
| `preset` | `string` | `validate_caption_style`: `bold_outline` \| `boxed` \| `minimal` \| `no_outline` | `caption_style_preset` |
| `font_family` | `string` | `validate_caption_font_family` | `caption_style_font_family` |

Nas **respostas**, o objeto sempre traz o valor **efetivo**, resolvido campo a
campo na ordem Clip, Job, default. Nunca vem `null`.

### 0.4 `ClipOut` (alterado em todas as respostas)
Os campos atuais **não mudam**. Entram dois novos.

| Campo | Tipo | Observação |
|---|---|---|
| `id`, `video_id`, `job_id` | `string` | |
| `index` | `integer` | |
| `start_time`, `end_time` | `number` | |
| `score` | `number` | |
| `processed_path` | `string \| null` | depois de uma edição, termina em `_r{n}.mp4` |
| `transcript_text` | `string \| null` | |
| `hashtags` | `object \| null` | |
| `suggested_titles` | `string[] \| null` | |
| `video_duration` | `number \| null` | |
| `has_streamer_region` | `boolean` | |
| **`caption_style`** | `ClipCaptionStyle` | **novo**, valores efetivos |
| **`revision`** | `integer` | **novo**, começa em `0` |

Exemplo:
```json
{
  "id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "video_id": "0f1e2d3c4b5a69788796a5b4c3d2e1f0",
  "job_id": "d703c5b6a1e24f0b9c8d7e6f5a4b3c21",
  "index": 0,
  "start_time": 4.0,
  "end_time": 24.0,
  "score": 87.5,
  "processed_path": "outputs/d703c5b6a1e24f0b9c8d7e6f5a4b3c21/clip_0_r5.mp4",
  "transcript_text": "Olá pessoal, isso é incrível",
  "hashtags": { "tiktok": ["#cortes"] },
  "suggested_titles": ["Isso é incrível"],
  "video_duration": 312.48,
  "has_streamer_region": false,
  "caption_style": { "enabled": true, "font_size": 8, "color": "#FFFFFF",
                     "preset": "bold_outline", "font_family": "DejaVu Sans" },
  "revision": 5
}
```

### 0.5 Lock por clipe e arquivo `_r{n+1}.mp4`
Valem para **toda** renderização (endpoints 1, 4, 5, 6 e 7):
- **Lock em memória por `clip_id`**, com `acquire` não bloqueante. Se outra
  renderização do mesmo clipe estiver rodando: `409 render_in_progress`, na
  hora, sem renderizar. Funciona porque a API roda num processo só.
- **Arquivo novo a cada renderização:** `clip_{index}_r{revision+1}.mp4`, na
  mesma pasta do atual. O arquivo atual nunca é sobrescrito.
- **O banco é o único ponto de virada:** só depois do commit o
  `processed_path` passa a apontar para o arquivo novo. Aí o antigo é apagado
  (best-effort). Se o commit falhar, o arquivo novo é apagado e o banco
  continua no antigo.
- **`revision += 1`** em toda gravação bem-sucedida.

Exemplo do 409:
```json
{ "detail": { "code": "render_in_progress",
              "message": "Outra edição deste corte está renderizando. Tente de novo em alguns segundos." } }
```

---

## 1. `PUT /clips/{clip_id}/edit`

Recebe o **rascunho completo** do clipe, valida, remapeia, recalcula
palavrões, censura as falas do Whisper, renderiza **uma vez** e grava.

### 1.1 Parâmetros

| Onde | Nome | Tipo | Obrigatório | Default | Limites |
|---|---|---|---|---|---|
| path | `clip_id` | `string` | sim | | |
| query | `dry_run` | `boolean` | não | `false` | |

### 1.2 Entrada (`ClipEditIn`)

```json
{
  "revision": 4,
  "range": { "start": 4.0, "end": 24.0 },
  "caption_style": { "enabled": true, "font_size": 8, "color": "#FFFFFF",
                     "preset": "bold_outline", "font_family": "DejaVu Sans" },
  "captions": {
    "mode": "manual",
    "lines": [
      { "start": 5.97, "end": 9.43, "text": "Olá pessoal" },
      { "start": 9.5, "end": 12.1, "text": "Isso é incrível!" }
    ]
  },
  "pan": [ { "t": 5.0, "cx": 0.2 }, { "t": 13.0, "cx": 0.8 } ],
  "removed": [ { "start": 15.0, "end": 17.0 } ],
  "confirm_losses": false
}
```

| Campo | Tipo | Obrigatório | Regra |
|---|---|---|---|
| `revision` | `integer` | sim | a versão em que o rascunho se baseou; precisa ser igual a `Clip.revision` |
| `range.start` | `number` | sim | `≥ 0` |
| `range.end` | `number` | sim | `≤ video_duration`; `end − start ≥ 0.5` |
| `caption_style` | `ClipCaptionStyle` | sim, com os 5 campos | gravado nas 5 colunas `caption_style_*`; o clipe deixa de herdar do Job |
| `captions.mode` | `"auto" \| "manual"` | sim | `auto` = falas do Whisper, censuradas se o filtro estiver ligado |
| `captions.lines` | `[{start, end, text}]` | sim em `manual`; **ignorado** em `auto` | `text.strip()` não vazio; `end > start` |
| `pan` | `[{t, cx}]` | sim | `[]` = enquadramento automático; `0 ≤ cx ≤ 1`; `t` sem repetição; lista não vazia em job com tela dividida é erro |
| `removed` | `[{start, end}]` | sim | `[]` = nada removido; só os trechos **do usuário**; `end > start`; o backend ordena e mescla sobreposições |
| `confirm_losses` | `boolean` | não | default `false` |

- **Substituição total.** Não existe "campo omitido = manter o que estava".
- **Trechos do filtro de palavrões nunca são enviados.** O backend sempre os
  recalcula para a janela atual. Um trecho em `removed` que coincida com um do
  filtro é tratado como remoção do usuário.

### 1.3 Saída `200` (renderizado) (`ClipEditOut`)

```json
{
  "status": "rendered",
  "clip": { "…ClipOut (§0.4), com revision = 5 e o caption_style gravado…": "" },
  "draft": { "…mesmo formato do GET /draft (§2), já normalizado e remapeado…": "" },
  "adjustments": [
    { "kind": "caption", "start": 22.1, "end": 24.0, "text": "Vamos lá",
      "change": "fim cortado em 24.0s" },
    { "kind": "pan", "start": 24.0, "end": 24.0,
      "change": "ponto interpolado criado na borda (24.0s)" }
  ],
  "render_seconds": 9.8
}
```

| Campo | Tipo | Observação |
|---|---|---|
| `status` | `"rendered" \| "dry_run"` | |
| `clip` | `ClipOut` | estado **depois** da gravação |
| `draft` | `ClipDraftOut` | o que a tela deve passar a mostrar; substitui o rascunho local |
| `adjustments` | `EditChange[]` | itens cortados na borda ou criados pelo backend; não precisam de confirmação |
| `render_seconds` | `number \| null` | tempo do ffmpeg; `null` no `dry_run` |

**`EditChange`** (usado em `adjustments` e em `losses`):

| Campo | Tipo | Observação |
|---|---|---|
| `kind` | `"caption" \| "pan" \| "removed"` | |
| `start`, `end` | `number` | absolutos; para `pan`, `start = end = t` |
| `text` | `string` | só em `caption` |
| `change` | `string` (PT) | só em `adjustments` |

### 1.4 `?dry_run=true`

- Passa pelas **mesmas** validações e checagens do PUT real, inclusive
  `revision_conflict`, `nothing_left` e `losses_require_confirmation`.
- **Para antes de renderizar:** não chama o ffmpeg, não grava no banco, não
  incrementa `revision`, não cria arquivo.
- Não usa o lock por clipe (não renderiza, então não disputa o arquivo).
- Serve para testes e diagnóstico. A tela não depende dele.

```json
{
  "status": "dry_run",
  "clip": { "…ClipOut ATUAL, sem nenhuma alteração (revision continua 4)…": "" },
  "draft": { "…como ficaria depois de gravar…": "" },
  "adjustments": [ ],
  "render_seconds": null
}
```

### 1.5 Erros

**Ordem das checagens** (a primeira que falhar responde):
validação (`400`) → `not_found` → `clip_not_rendered` → lock
(`render_in_progress`) → `revision_conflict` → remapeamento →
`nothing_left` → `losses_require_confirmation` → renderização (`500`).

| HTTP | `code` | Quando | Extras em `detail` | O que mudou |
|---|---|---|---|---|
| 400 | `invalid_range` | `end ≤ start`, fora de `[0, video_duration]`, ou duração < 0.5s | | nada |
| 400 | `invalid_caption` | texto vazio, ou `end ≤ start` numa linha (só em `manual`) | `index` | nada |
| 400 | `invalid_pan` | `cx` fora de 0..1, `t` repetido, ou zoom manual em job com tela dividida | `index` (quando se aplica) | nada |
| 400 | `invalid_style` | cor, fonte, formato ou tamanho inválidos | `field` | nada |
| 400 | `nothing_left` | os trechos removidos (usuário + filtro) deixam menos de 0.5s de vídeo | | nada |
| 404 | `not_found` | clipe ou vídeo original inexistente | | nada |
| 409 | `clip_not_rendered` | o clipe ainda não tem arquivo gerado | | nada |
| 409 | `render_in_progress` | outra renderização do mesmo clipe está rodando | | nada |
| 409 | `revision_conflict` | `revision` diferente da atual: outra aba salvou antes | `current_revision` | nada |
| 409 | `losses_require_confirmation` | há perdas e `confirm_losses` é `false` | `losses: EditChange[]`, `framing_fallback: boolean` | nada |
| 422 | (padrão do FastAPI) | JSON mal formado ou tipo errado | | nada |
| 500 | `render_failed` | o ffmpeg falhou, ou o commit no banco falhou | `ffmpeg_tail` (últimas linhas do stderr, se disponíveis) | **nada**: arquivo e banco anteriores intactos |

**Exemplos:**

`400 invalid_caption`
```json
{ "detail": { "code": "invalid_caption", "index": 1,
              "message": "A linha 2 da legenda está sem texto." } }
```

`409 losses_require_confirmation`, com a lista do que vai sumir
```json
{ "detail": {
    "code": "losses_require_confirmation",
    "message": "3 itens ficaram fora do novo corte e serão descartados.",
    "losses": [
      { "kind": "caption", "start": 1.2, "end": 3.8, "text": "Bom dia a todos" },
      { "kind": "pan", "start": 2.0, "end": 2.0 },
      { "kind": "removed", "start": 25.0, "end": 26.5 }
    ],
    "framing_fallback": false } }
```
O frontend mostra o modal. Se o usuário confirmar, **reenvia o mesmo
corpo** com `confirm_losses: true`.

`framing_fallback` é `true` quando o rascunho tinha pontos de zoom e nenhum
chega ao render, seja por estarem fora do corte ou dentro de trechos que serão
cortados: o vídeo sai com o enquadramento automático, e o modal avisa. É só
informativo e vem igual com `?dry_run=true`. Um `dry_run` sem perdas responde
`200` e não traz o campo.

`409 revision_conflict`
```json
{ "detail": { "code": "revision_conflict", "current_revision": 6,
              "message": "Este corte foi alterado em outra aba. Recarregue para ver a versão atual." } }
```

`409 render_in_progress`: ver §0.5.

`409 clip_not_rendered`
```json
{ "detail": { "code": "clip_not_rendered",
              "message": "Este corte ainda não tem um vídeo gerado." } }
```

`500 render_failed`
```json
{ "detail": { "code": "render_failed",
              "message": "A renderização falhou. Nada foi alterado; o corte anterior continua valendo.",
              "ffmpeg_tail": "…\nConversion failed!" } }
```

### 1.6 Notas de comportamento

**Sequência**, conforme a especificação, §5:
1. Validar.
2. Lock por clipe.
3. Conferir `revision`.
4. Remapear em memória.
5. Perdas sem confirmação: responde 409.
6. Recalcular palavrões para a nova janela.
7. Aplicar `redact_text` **só nas falas do Whisper**.
8. Se `dry_run`, para e responde.
9. Renderizar em `clip_{index}_r{revision+1}.mp4`.
10. Compare-and-swap no banco:
    `UPDATE clips SET …, revision = revision + 1 WHERE id = ? AND revision = ?`.
    - 0 linhas afetadas: apaga o arquivo novo e responde `409 revision_conflict`.
11. Commit, apaga o arquivo antigo, libera o lock.

**Atomicidade:**
- Uma falha nos passos 1 a 9 não muda nada.
- No passo 10, ou tudo vira (banco + arquivo novo), ou nada vira.
- Se o processo morrer entre o render e o commit, sobra um `_r*.mp4` órfão,
  que não afeta o usuário.

**Censura:**
- Com `Job.profanity_filter` em `"cut"` ou `"censor"`, toda fala vinda do
  Whisper passa pelo `redact_text`. Isso vale no modo `auto` e nas falas que
  entram na área nova de um corte que cresceu (modo `manual`).
- O texto digitado pelo usuário (modo `manual`) **nunca** é censurado.
- Os trechos mudos (`censor`) ou cortados (`cut`) são recalculados do zero para
  a nova janela e nunca contam como perda.

**Remapeamento** (regras completas na especificação, §4):

| Item | Totalmente fora | Parcialmente fora | Área nova (corte cresceu) |
|---|---|---|---|
| Legenda manual | perda | cortada na borda (ajuste) | recebe as falas do Whisper, censuradas |
| Ponto de zoom | perda | ponto interpolado na borda (ajuste) | nada |
| Trecho removido pelo usuário | perda | cortado na borda (ajuste) | nada |

**O que é gravado no sucesso.** Os valores são relativos ao início do clipe
(`t − range.start`), nas coordenadas **antes** dos cortes internos, como já é
hoje:

| Coluna | Valor |
|---|---|
| `start_time`, `end_time` | `range` |
| `caption_style_*` (5) | `caption_style` |
| `caption_segments_override` | linhas do modo `manual`, já remapeadas; NULL no modo `auto` |
| `manual_pan_keyframes` | `pan` remapeado; NULL se vazio |
| `manual_removed_ranges` | `removed` remapeado; NULL se vazio |
| `cut_segments` | trechos **mantidos** = complemento de (`removed` ∪ cortes do filtro); NULL se não houver buraco |
| `mute_ranges` | trechos mudos do filtro (modo `censor`); NULL se não houver |
| `processed_path` | o novo `clip_{index}_r{revision+1}.mp4` |
| `revision` | `revision + 1` |
| `transcript_text`, `hashtags`, `suggested_titles` | recalculados a partir do texto que sobrou |

---

## 2. `GET /clips/{clip_id}/draft`

Carrega o estado completo do clipe no **mesmo formato da entrada do `/edit`**,
mais campos só de leitura. Substitui as 4 chamadas atuais da tela.

### 2.1 Parâmetros

| Onde | Nome | Tipo | Obrigatório |
|---|---|---|---|
| path | `clip_id` | `string` | sim |

Não tem query nem corpo de entrada.

### 2.2 Saída `200` (`ClipDraftOut`)

```json
{
  "clip_id": "a1b2c3d4e5f60718293a4b5c6d7e8f90",
  "job_id": "d703c5b6a1e24f0b9c8d7e6f5a4b3c21",
  "revision": 4,
  "video_duration": 312.48,
  "has_streamer_region": false,

  "range": { "start": 4.0, "end": 24.0 },
  "caption_style": { "enabled": true, "font_size": 8, "color": "#FFFFFF",
                     "preset": "bold_outline", "font_family": "DejaVu Sans" },
  "captions": {
    "mode": "manual",
    "lines": [ { "start": 5.97, "end": 9.43, "text": "Olá pessoal" } ]
  },
  "pan": [ { "t": 5.0, "cx": 0.2 }, { "t": 13.0, "cx": 0.8 } ],
  "removed": [ { "start": 15.0, "end": 17.0 } ],

  "profanity": {
    "mode": "censor",
    "ranges": [ { "start": 19.2, "end": 19.61, "kind": "muted" } ]
  }
}
```

| Campo | Tipo | Origem / regra |
|---|---|---|
| `clip_id`, `job_id` | `string` | |
| `revision` | `integer` | `Clip.revision`; o frontend devolve esse valor no `/edit` |
| `video_duration` | `number \| null` | duração do vídeo original |
| `has_streamer_region` | `boolean` | `true` = a trilha Zoom fica oculta, com aviso |
| `range` | `{start, end}` | `Clip.start_time`, `Clip.end_time` |
| `caption_style` | `ClipCaptionStyle` | valores efetivos |
| `captions.mode` | `"auto" \| "manual"` | `"manual"` se `caption_segments_override` não for NULL |
| `captions.lines` | `[{start, end, text}]` | `manual`: o override convertido para absoluto, **sem censura**. `auto`: as falas do Whisper no `range` (`segments_overlapping`, ≥ 50%), **censuradas** se o filtro estiver ligado. Vem preenchido nos dois modos, para a trilha desenhar. |
| `pan` | `[{t, cx}]` | `manual_pan_keyframes` convertido para absoluto; `[]` se NULL |
| `removed` | `[{start, end}]` | `manual_removed_ranges` convertido para absoluto; `[]` se NULL (ver §2.4 para clipes antigos) |
| `profanity.mode` | `"off" \| "cut" \| "censor"` | `Job.profanity_filter` |
| `profanity.ranges` | `[{start, end, kind}]` | **só leitura**, recalculado para o `range` atual; `kind` é `"muted"` (censor) ou `"cut"` (cut); `[]` se `mode = "off"` |

Todas as listas vêm em ordem crescente de `start` (ou `t`).

### 2.3 Erros

| HTTP | `code` | Quando |
|---|---|---|
| 404 | `not_found` | clipe inexistente |

```json
{ "detail": { "code": "not_found", "message": "Corte não encontrado." } }
```

### 2.4 Notas de comportamento

- **Só leitura:** não grava nada, não usa lock, não mexe em `revision`.
- **Clipes antigos**, editados pelo `PUT /cuts` antigo, têm `cut_segments`
  preenchido e `manual_removed_ranges` NULL. Para eles, o `/draft` deriva
  `removed` assim:
  1. pega os buracos de `cut_segments` (o complemento dos trechos mantidos);
  2. se `profanity.mode = "cut"`, subtrai os `profanity.ranges`;
  3. descarta sobras menores que 0,05s.

  Para essa derivação continuar certa, o `PUT /cuts` antigo zera
  `manual_removed_ranges` (ver §7). O `recut` **não** zera: ele usa essa mesma
  derivação como ponto de partida e remapeia o resultado (ver §4.5).
- **Censura:** igual ao `/edit`, só as falas do Whisper.

---

## 3. `GET /jobs/{job_id}/words`

Palavras com timestamp para o snap da timeline, já censuradas quando o filtro
estiver ligado.

### 3.1 Parâmetros

| Onde | Nome | Tipo | Obrigatório | Default | Limites |
|---|---|---|---|---|---|
| path | `job_id` | `string` | sim | | |
| query | `from` | `number` | não | `0` | valores fora de `[0, video_duration]` são ajustados ao limite |
| query | `to` | `number` | não | `from + 600` | idem; depois do ajuste, precisa valer `to > from`; **janela máxima de 600s** |

**Ajuste da janela, nesta ordem:**
1. Sem `from`: `from = 0`. Sem `to`: `to = from + 600`.
2. `from` e `to` são ajustados a `[0, video_duration]`.
3. Se `to ≤ from`: `400 invalid_range`.
4. Se `to − from > 600`: `to = from + 600` e `truncated = true`.

Sem `from`/`to` **não** vem mais o vídeo inteiro: vêm os primeiros 600s.
Para cobrir um trecho maior, o frontend faz chamadas seguidas, começando cada
uma no `to` devolvido pela anterior. Na v1, a janela da tela (clipe de até
60s + 60s de folga) sempre cabe numa chamada só.

Não tem corpo de entrada.

### 3.2 Saída `200` (`WordsOut`)

```json
{
  "job_id": "d703c5b6a1e24f0b9c8d7e6f5a4b3c21",
  "language": "pt",
  "from": 4.0,
  "to": 24.0,
  "truncated": false,
  "censored": true,
  "words": [
    { "start": 4.12, "end": 4.38, "text": "olá", "is_censored": false },
    { "start": 4.4, "end": 4.95, "text": "pessoal", "is_censored": false },
    { "start": 5.1, "end": 5.52, "text": "*******", "is_censored": true }
  ]
}
```

| Campo | Tipo | Regra |
|---|---|---|
| `job_id` | `string` | |
| `language` | `string \| null` | `Job.language` ou, se vazio, o `language` do `transcript.json` |
| `from`, `to` | `number` | os valores **já ajustados** (aos limites do vídeo e à janela de 600s) |
| `truncated` | `boolean` | `true` se a janela pedida passou de 600s e o `to` foi cortado para `from + 600`; ajustar aos limites do vídeo **não** conta como truncar |
| `censored` | `boolean` | `true` se `Job.profanity_filter` for `"cut"` ou `"censor"` |
| `words` | `WordOut[]` | toda palavra com `end > from` e `start < to`, em ordem de `start` |
| `words[].start`, `words[].end` | `number` | absolutos, 3 casas |
| `words[].text` | `string` | sem os espaços nas pontas que o Whisper coloca; com `censored = true`, passa pelo `redact_text` |
| `words[].is_censored` | `boolean` | `true` se esta palavra foi trocada por asteriscos; sempre `false` quando `censored = false` |

### 3.3 Erros

| HTTP | `code` | Quando |
|---|---|---|
| 400 | `invalid_range` | `to ≤ from` depois do ajuste aos limites |
| 404 | `not_found` | job inexistente |
| 404 | `transcript_not_found` | o job terminou (`completed` ou `failed`) e não existe `transcript.json` |
| 409 | `transcription_not_ready` | o job ainda está em `pending` ou `running` e o `transcript.json` ainda não existe |

```json
{ "detail": { "code": "transcription_not_ready",
              "message": "A transcrição deste vídeo ainda está em andamento." } }
```
```json
{ "detail": { "code": "transcript_not_found",
              "message": "Este vídeo não tem transcrição salva." } }
```

### 3.4 Notas de comportamento

- **Só leitura.** O texto bruto do `transcript.json` não sai pela API quando o
  filtro está ligado.
- Vídeo sem fala, ou `transcript.json` antigo sem o campo `words`:
  `words: []`. É uma resposta `200` válida, e o frontend desliga o snap em
  silêncio.
- **Cache:** o transcript não muda depois do processamento, então a resposta
  `200` vai com `Cache-Control: private, max-age=86400`. Erros não levam esse
  cabeçalho.
- **Tamanho típico:** a janela de um clipe (20–60s + folga) tem 100–200
  palavras, uns 5–10 KB. O frontend pede só a janela visível (corte ± 30s).

---

## 4. `POST /clips/{clip_id}/recut` (alterado)

Reajusta o tempo do corte e/ou o estilo da legenda e renderiza de novo.

### 4.1 Parâmetros
| Onde | Nome | Tipo | Obrigatório |
|---|---|---|---|
| path | `clip_id` | `string` | sim |

### 4.2 Entrada (`RecutRequest`, **sem mudança**)
Todos os campos são opcionais; `null` ou ausente mantém o valor atual.
```json
{ "start_time": 4.0, "end_time": 24.0,
  "captions_enabled": true, "caption_font_size": 8, "caption_color": "#FFFFFF",
  "caption_style": "bold_outline", "caption_font_family": "DejaVu Sans" }
```
| Campo | Tipo | Regra |
|---|---|---|
| `start_time`, `end_time` | `number \| null` | relativos ao vídeo original (como hoje); `end > start`; dentro da duração |
| `captions_enabled` | `boolean \| null` | |
| `caption_font_size` | `integer \| null` | entre `CAPTION_FONT_SIZE_MIN` e `MAX` |
| `caption_color` | `string \| null` | `#RRGGBB` |
| `caption_style` | `string \| null` | preset |
| `caption_font_family` | `string \| null` | |

### 4.3 Saída `200`
`ClipOut` (§0.4), agora com `caption_style` e `revision`.

### 4.4 Erros
| HTTP | Formato | Quando |
|---|---|---|
| 400 | antigo (`{"detail": "texto"}`) | `end_time ≤ start_time`, fora da duração, estilo inválido |
| 404 | antigo | clipe ou vídeo original inexistente |
| 409 | antigo | clipe ainda sem arquivo processado |
| 409 | **novo**, `render_in_progress` | outra renderização do mesmo clipe está rodando (§0.5) |
| 500 | padrão do FastAPI | falha no ffmpeg (como hoje) |

### 4.5 O que muda
- `revision += 1` no sucesso.
- O arquivo novo é `_r{n+1}.mp4`, com o lock por clipe (§0.5).
- Os campos de estilo **omitidos** caem para o estilo **efetivo do clipe**,
  não mais só para o do Job.
- **Remapeia em vez de apagar.** O contrato externo **não muda**: mesma
  entrada, mesma saída e o mesmo formato de erro antigo. O que muda é o
  comportamento interno:
  - quando o tempo muda, o `recut` usa o **mesmo módulo de remapeamento do
    `/edit`** (`services/clip_edit.py`), com as regras da §1.6;
  - legenda manual, pontos de zoom e trechos removidos pelo usuário que
    continuam dentro do novo corte **sobrevivem**, com o mesmo tempo absoluto;
  - os que ficam parcialmente fora são cortados na borda, com ponto de zoom
    interpolado na borda;
  - **confirmação implícita:** como não há `confirm_losses` nem modal na tela
    antiga, o `recut` age como `confirm_losses: true`, e o que fica
    **totalmente** fora é descartado;
  - na área nova de um corte que cresceu, a legenda manual recebe as falas do
    Whisper, **censuradas** se o filtro estiver ligado;
  - os palavrões (`mute_ranges` e os cortes do filtro) são recalculados para a
    nova janela;
  - `manual_removed_ranges` é remapeado, não zerado. Num clipe antigo, em que
    ele é NULL, o ponto de partida é a derivação da §2.4.
- **Grava o estilo enviado.** Cada campo de estilo **presente** no corpo é
  gravado na coluna correspondente do Clip:

  | Campo do `RecutRequest` | Coluna do Clip |
  |---|---|
  | `captions_enabled` | `caption_style_enabled` |
  | `caption_font_size` | `caption_style_font_size` |
  | `caption_color` | `caption_style_color` |
  | `caption_style` | `caption_style_preset` |
  | `caption_font_family` | `caption_style_font_family` |

  Os campos ausentes ou `null` não mexem na coluna. Sem isso, um `PUT /pan`
  depois do `recut` voltaria ao estilo do Job.

---

## 5. `PUT /clips/{clip_id}/pan` (alterado)

Grava o rastro de pan manual e renderiza de novo.

### 5.1 Parâmetros
| Onde | Nome | Tipo | Obrigatório |
|---|---|---|---|
| path | `clip_id` | `string` | sim |

### 5.2 Entrada (`PanKeyframesIn`, **sem mudança**)
```json
{ "keyframes": [ { "t": 1.0, "cx": 0.2 }, { "t": 9.0, "cx": 0.8 } ] }
```
| Campo | Tipo | Regra |
|---|---|---|
| `keyframes[].t` | `number` | segundos **relativos ao início do clipe**; `0 ≤ t ≤ duração do clipe`; estritamente crescente |
| `keyframes[].cx` | `number` | `0 ≤ cx ≤ 1` |
| `keyframes` | lista | `[]` = volta à detecção automática |

### 5.3 Saída `200`
`ClipOut` (§0.4).

### 5.4 Erros
| HTTP | Formato | Quando |
|---|---|---|
| 400 | antigo | `cx` ou `t` inválidos; job com tela dividida |
| 404 | antigo | clipe ou vídeo original inexistente |
| 409 | antigo | clipe ainda sem arquivo processado |
| 409 | **novo**, `render_in_progress` | §0.5 |

### 5.5 O que muda
- `revision += 1` no sucesso.
- O arquivo novo é `_r{n+1}.mp4`, com o lock por clipe.
- A renderização usa o estilo **efetivo do clipe**.
- O `GET /clips/{id}/pan` **não muda**.

---

## 6. `PUT /clips/{clip_id}/captions` (alterado)

Grava o override manual da legenda e renderiza de novo.

### 6.1 Parâmetros
| Onde | Nome | Tipo | Obrigatório |
|---|---|---|---|
| path | `clip_id` | `string` | sim |

### 6.2 Entrada (`CaptionLinesIn`, **sem mudança**)
```json
{ "segments": [ { "start": 1.97, "end": 5.43, "text": "Olá pessoal" } ] }
```
| Campo | Tipo | Regra |
|---|---|---|
| `segments[].start`, `end` | `number` | relativos ao início do clipe; `start ≥ 0`; `end > start` |
| `segments[].text` | `string` | não vazio |
| `segments` | lista | `[]` = remove o override e volta às falas do Whisper |

### 6.3 Saída `200`
`ClipOut` (§0.4).

### 6.4 Erros
| HTTP | Formato | Quando |
|---|---|---|
| 400 | antigo | texto vazio, `start < 0`, `end ≤ start` |
| 404 | antigo | clipe ou vídeo original inexistente |
| 409 | antigo | clipe ainda sem arquivo processado |
| 409 | **novo**, `render_in_progress` | §0.5 |

### 6.5 O que muda
- `revision += 1` no sucesso.
- O arquivo novo é `_r{n+1}.mp4`, com o lock por clipe.
- A renderização usa o estilo **efetivo do clipe**.
- **Censura não é reaplicada** neste endpoint: fica fora da v1 (especificação,
  §11).
- O `GET /clips/{id}/captions` **não muda**.

---

## 7. `PUT /clips/{clip_id}/cuts` (alterado)

Grava os cortes internos (trechos **mantidos**) e renderiza de novo.

### 7.1 Parâmetros
| Onde | Nome | Tipo | Obrigatório |
|---|---|---|---|
| path | `clip_id` | `string` | sim |

### 7.2 Entrada (`CutSegmentsIn`, **sem mudança**)
```json
{ "keep_segments": [ [0.0, 11.0], [13.0, 20.0] ] }
```
| Campo | Tipo | Regra |
|---|---|---|
| `keep_segments` | `[[início, fim], …]` | relativos ao início do clipe; ordenados, sem sobreposição; cada trecho ≥ `MIN_KEEP_SEGMENT_DURATION`; dentro da duração do clipe. `[]`, ou um único trecho cobrindo o clipe inteiro, remove os cortes |

### 7.3 Saída `200`
`ClipOut` (§0.4).

### 7.4 Erros
| HTTP | Formato | Quando |
|---|---|---|
| 400 | antigo | trecho inválido, curto demais, fora dos limites ou sobreposto |
| 404 | antigo | clipe ou vídeo original inexistente |
| 409 | antigo | clipe ainda sem arquivo processado |
| 409 | **novo**, `render_in_progress` | §0.5 |

### 7.5 O que muda
- `revision += 1` no sucesso.
- O arquivo novo é `_r{n+1}.mp4`, com o lock por clipe.
- A renderização usa o estilo **efetivo do clipe**.
- Zera `manual_removed_ranges` (NULL), para o `/draft` passar a derivar o
  `removed` dos buracos de `cut_segments` (§2.4).
- O `GET /clips/{id}/cuts` **não muda**.

---

## 8. `GET /clips/{clip_id}/metrics` (não muda)

**Não é tocado na v1.** Ele devolve `MetricOut[]` (não `ClipOut`), não
renderiza e não mexe em `revision`. Também não mudam:
`POST /clips/{clip_id}/metrics` e `POST /clips/{clip_id}/metrics/youtube-sync`.

---

## 9. Tipos TypeScript de referência (Agente C)

```ts
export type CaptionPreset = "bold_outline" | "boxed" | "minimal" | "no_outline";

export interface ClipCaptionStyle {
  enabled: boolean;
  font_size: number;
  color: string;        // "#RRGGBB"
  preset: CaptionPreset;
  font_family: string;
}

// ClipOut existente ganha:
//   caption_style: ClipCaptionStyle;
//   revision: number;

export interface TimeRange { start: number; end: number; }
export interface CaptionLineAbs { start: number; end: number; text: string; }
export interface PanPointAbs { t: number; cx: number; }
export interface ProfanityRange extends TimeRange { kind: "muted" | "cut"; }

export interface ClipDraft {
  clip_id: string;
  job_id: string;
  revision: number;
  video_duration: number | null;
  has_streamer_region: boolean;
  range: TimeRange;
  caption_style: ClipCaptionStyle;
  captions: { mode: "auto" | "manual"; lines: CaptionLineAbs[] };
  pan: PanPointAbs[];
  removed: TimeRange[];
  profanity: { mode: "off" | "cut" | "censor"; ranges: ProfanityRange[] };
}

export interface ClipEditIn {
  revision: number;
  range: TimeRange;
  caption_style: ClipCaptionStyle;
  captions: { mode: "auto" | "manual"; lines: CaptionLineAbs[] };
  pan: PanPointAbs[];
  removed: TimeRange[];
  confirm_losses?: boolean;
}

export interface EditChange {
  kind: "caption" | "pan" | "removed";
  start: number;
  end: number;
  text?: string;
  change?: string;   // só em adjustments
}

export interface ClipEditOut {
  status: "rendered" | "dry_run";
  clip: ClipOut;
  draft: ClipDraft;
  adjustments: EditChange[];
  render_seconds: number | null;
}

export interface ApiErrorDetail {
  code: string;
  message: string;
  losses?: EditChange[];
  framing_fallback?: boolean;
  current_revision?: number;
  index?: number;
  field?: string;
  ffmpeg_tail?: string;
}

export interface WordOut { start: number; end: number; text: string; is_censored: boolean; }

export interface WordsOut {
  job_id: string;
  language: string | null;
  from: number;
  to: number;
  truncated: boolean;
  censored: boolean;
  words: WordOut[];
}
```

## 10. Nomes dos schemas Pydantic (Agente B, em `schemas.py`)

`ClipCaptionStyle`, `TimeRange`, `CaptionLineAbs`, `PanPointAbs`,
`ProfanityRange`, `ClipDraftOut`, `ClipEditIn`, `EditChange`, `ClipEditOut`,
`WordOut`, `WordsOut`.

- `from` é palavra reservada em Python: o campo de `WordsOut` se chama
  `from_` com `Field(alias="from")` e é serializado com `by_alias=True`.
- `ClipOut` ganha `caption_style: ClipCaptionStyle` e `revision: int`.
