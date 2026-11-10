# Especificação: Timeline de Revisão v1

> **Fonte de verdade** para os agentes A, B, C e D. O contrato detalhado de
> cada endpoint (campos, tipos, erros, exemplos) está em
> [`api-contract-timeline-v1.md`](api-contract-timeline-v1.md). Se este
> documento e o contrato discordarem, **pare e avise**: não escolha um dos dois.
>
> Se algo que você precisa **não estiver** nos dois documentos, pergunte antes
> de assumir. Se o contrato precisar mudar, pare tudo e avise.

**Em uma frase:** a v1 troca "cada ajuste renderiza na hora, e mudar o corte
apaga as outras edições" por "tudo é editado na tela, sem esperar, e
renderizado uma única vez, sem perder nada sem avisar".

---

## 1. Princípios

1. **O frontend só trabalha com tempos ABSOLUTOS**, em segundos do vídeo
   original. A conversão para o formato relativo que o banco usa acontece
   **só no backend**, num único módulo (`services/clip_edit.py`) coberto
   por testes.
2. **Uma renderização por clique.** O `PUT /clips/{id}/edit` recebe o
   rascunho completo e renderiza uma vez só.
3. **Nenhuma perda silenciosa.** Tudo o que vai sumir passa pelo
   `409 losses_require_confirmation` e pelo modal de confirmação.
4. **Censura sempre reaplicada.** O `transcript.json` guarda o texto **bruto**,
   e hoje o `redact_text` só roda no pipeline inicial (`pipeline.py:231`).
   O `/edit` e o `/words` aplicam o `redact_text` às falas do Whisper sempre
   que `Job.profanity_filter` for `"cut"` ou `"censor"`.
   - **Só nas falas do Whisper.** O texto digitado à mão pelo usuário
     (legenda em modo `manual`) **não** é censurado, porque é intencional.
5. **Tempos com no máximo 3 casas decimais** em toda resposta da API.

## 2. Banco de dados

### 2.1 Colunas novas em `clips`

| Coluna | Tipo SQLAlchemy | Tipo SQLite | Nulo | Default | Significado |
|---|---|---|---|---|---|
| `caption_style_enabled` | `Boolean` | `BOOLEAN` | sim | NULL | legenda ligada |
| `caption_style_font_size` | `Integer` | `INTEGER` | sim | NULL | tamanho da fonte |
| `caption_style_color` | `String` | `VARCHAR` | sim | NULL | `#RRGGBB` |
| `caption_style_preset` | `String` | `VARCHAR` | sim | NULL | `bold_outline` \| `boxed` \| `minimal` \| `no_outline` |
| `caption_style_font_family` | `String` | `VARCHAR` | sim | NULL | uma de `CAPTION_FONT_FAMILIES` |
| `revision` | `Integer` | `INTEGER` | **não** | `0` | versão do clipe, usada no compare-and-swap |
| `manual_removed_ranges` | `Text` | `TEXT` | sim | NULL | trechos removidos **pelo usuário** |

**Estilo, regra de herança:** NULL quer dizer "herda do Job". O valor
efetivo de cada campo é resolvido **campo a campo**, nesta ordem:
Clip, depois Job, depois o default fixo (`True`, `8`, `#FFFFFF`,
`bold_outline`, `DejaVu Sans`). Quem resolve é uma função pura em
`services/caption_style.py`, usada por **todos** os caminhos de
renderização. Por isso o `pipeline.py` não muda e clipes antigos funcionam
sem backfill.

**`manual_removed_ranges`:** JSON `[[início, fim], ...]` em segundos
**relativos ao início do clipe** (a mesma convenção de `cut_segments`),
ordenado e sem sobreposição. NULL ou `[]` significam que não há remoção
manual. A coluna existe para separar o que o usuário removeu do que o filtro
de palavrões cortou: hoje os dois ficam misturados em `cut_segments`, e o
recálculo dos palavrões ao mudar o corte poderia duplicar ou perder cortes
do usuário.

**`cut_segments` continua existindo** e continua sendo o que o renderizador
usa (lista de trechos **mantidos**). No `/edit`, ele passa a ser **derivado**:
`cut_segments = complemento(manual_removed_ranges ∪ cortes_do_filtro)`
dentro da janela do clipe.

### 2.2 Criação e migração

- **Banco novo:** o `init_db()` (`Base.metadata.create_all`) já cria a tabela
  com as 7 colunas. O banco atual foi recriado vazio em 06/11.
- **Banco existente:** o `create_all` **não** adiciona colunas a uma tabela
  que já existe. O README ganha a seção **"Atualizando banco existente"**:
  1. copiar `backend/data/app.db` para `app.db.bak-AAAAMMDD`;
  2. rodar:
     ```sql
     ALTER TABLE clips ADD COLUMN caption_style_enabled     BOOLEAN;
     ALTER TABLE clips ADD COLUMN caption_style_font_size   INTEGER;
     ALTER TABLE clips ADD COLUMN caption_style_color       VARCHAR;
     ALTER TABLE clips ADD COLUMN caption_style_preset      VARCHAR;
     ALTER TABLE clips ADD COLUMN caption_style_font_family VARCHAR;
     ALTER TABLE clips ADD COLUMN revision INTEGER NOT NULL DEFAULT 0;
     ALTER TABLE clips ADD COLUMN manual_removed_ranges     TEXT;
     ```
  3. ou, se o banco não tiver nada importante, apagar o `app.db`.
- **Rollback:** um `git revert` do código **não exige** mexer no banco,
  porque o código antigo ignora as colunas extras. Para removê-las, o
  SQLite 3.53.1 da venv aceita `ALTER TABLE clips DROP COLUMN …`.

## 3. Endpoints

Detalhes completos em `api-contract-timeline-v1.md`.

| Endpoint | Novo? | Resumo |
|---|---|---|
| `GET /clips/{id}/draft` | novo | carrega tudo o que a tela precisa numa chamada só (hoje são 4) |
| `PUT /clips/{id}/edit` (`?dry_run=true`) | novo | recebe o rascunho completo, valida, remapeia, censura, renderiza e grava |
| `GET /jobs/{id}/words` | novo | palavras com timestamp para o snap, já censuradas (`censored` no topo, `is_censored` em cada palavra); janela máxima de 600s, com `truncated` |
| `ClipOut` (todas as respostas de clipe) | alterado | ganha `caption_style` (efetivo) e `revision` |
| `POST /clips/{id}/recut`, `PUT /clips/{id}/cuts`, `PUT /clips/{id}/pan`, `PUT /clips/{id}/captions` | alterados | `revision += 1` a cada gravação; usam o estilo do clipe; ver §6 |

## 4. Regras de remapeamento (PUT /edit)

O backend compara cada item do rascunho com o **novo** `range`. Para saber
qual área é "nova", também compara com o intervalo **salvo** do clipe.

| Item | Totalmente fora do corte | Parcialmente fora | Corte cresceu (área nova) |
|---|---|---|---|
| Legenda manual | **perda** (precisa confirmar) | cortada na borda (ajuste, só informa) | recebe as falas do Whisper daquela área, **censuradas** |
| Ponto de zoom | **perda** | ver abaixo | mantém o último enquadramento (nada a fazer) |
| Trecho removido pelo usuário | **perda** | cortado na borda (ajuste) | nada |
| Palavrões (mudos ou cortados) | recalculados do zero para a nova janela; nunca entram como perda | | |

- **Zoom "parcialmente fora":** um ponto é instantâneo, então ou está dentro
  ou está fora. Quando há um ponto dentro e o vizinho fora, o ponto de fora é
  **perda** e o backend cria um ponto **interpolado exatamente na borda**
  (registrado em `adjustments`), para o enquadramento na borda não mudar.
- **"Exatamente na borda" conta como dentro:** `start ≤ t ≤ end`.
- **Nenhum ponto de zoom chega ao render:** o vídeo usa o enquadramento
  automático, sem congelar nem interpolar o enquadramento antigo (decisão de
  09/11). O diálogo de perdas avisa isso de forma explícita.
- **Legenda em modo `auto`:** não tem perda nem ajuste; é sempre recalculada
  a partir das falas do Whisper (`segments_overlapping`, ≥ 50% de
  sobreposição), censuradas.

## 5. Ordem atômica do PUT /edit

Arquivo e banco não cabem numa mesma transação. A troca é feita assim: o
banco é o único ponto de virada, e cada renderização gera um **arquivo novo**.

1. **Validar** o payload (400 com `code` específico).
2. **Lock por clipe** em memória, com `acquire` não bloqueante. Se outra
   renderização do mesmo clipe estiver rodando: `409 render_in_progress`.
   Funciona porque a API roda num processo só; se um dia houver vários, o
   compare-and-swap do passo 9 continua protegendo o banco.
3. **Conferir `revision`:** se for diferente de `clip.revision`,
   `409 revision_conflict`.
4. **Remapear** em memória (§4).
5. **Perdas:** se houver e `confirm_losses` for `false`,
   `409 losses_require_confirmation`.
6. **Recalcular palavrões** para a nova janela (modo `cut` gera cortes; modo
   `censor` gera trechos mudos).
7. **Aplicar `redact_text`** nas falas do Whisper (só nelas), se o filtro
   estiver ligado.
8. **`dry_run=true`?** Devolve 200 com o resultado calculado e **para**: sem
   renderizar, sem gravar, sem incrementar `revision`.
9. **Renderizar** num arquivo novo `clip_{index}_r{revision+1}.mp4`, na mesma
   pasta do atual. O arquivo atual não é tocado. Se o ffmpeg falhar, o
   temporário é apagado e a resposta é `500 render_failed`.
10. **Compare-and-swap no banco:**
    `UPDATE clips SET …, processed_path = novo, revision = revision + 1 WHERE id = ? AND revision = ?`.
    - 1 linha afetada: commit, depois apaga o arquivo antigo (best-effort).
    - 0 linhas afetadas: apaga o arquivo novo e responde `409 revision_conflict`.
    - Exceção no commit: rollback, apaga o arquivo novo e responde
      `500 render_failed`. O banco continua apontando para o arquivo antigo,
      que ainda existe.
11. Libera o lock (`finally`).

**O que cada falha deixa para trás:**
- **Passos 1 a 8:** nada muda.
- **Passo 9:** nada muda.
- **Passo 10:** nada muda, ou sobra um `_r*.mp4` órfão se o processo morrer
  no meio. O órfão não afeta o usuário.

## 6. Endpoints antigos

- **`revision += 1`** em toda gravação bem-sucedida de `recut`, `cuts`, `pan`
  e `captions`. Sem isso, eles passam por baixo do compare-and-swap.
- Também passam a gerar o arquivo `_r{n+1}.mp4`. A lógica fica centralizada no
  `render_clip`/`safe_rerender` (Agente A); a pasta e a regra de nome são as
  mesmas do `/edit`.
- Também passam a usar o **lock por clipe** (`409 render_in_progress`), já
  que agora todos geram arquivo novo.
- Renderizam com o **estilo efetivo do clipe** (§2.1), via `caption_style.py`.
- **Fora da v1** (ver §11): eles continuam **sem** reaplicar a censura.
- **O `recut` remapeia em vez de apagar** (decisão de 06/11). Ele usa o
  **mesmo módulo de remapeamento do `/edit`** (`services/clip_edit.py`, §4),
  para existir uma única implementação da regra.
  - O **contrato externo não muda**: mesma entrada (`RecutRequest`), mesma
    saída (`ClipOut`) e o mesmo formato de erro antigo (`{"detail": "texto"}`).
  - Muda o **comportamento interno**: legenda manual, pontos de zoom e trechos
    removidos pelo usuário que continuam dentro do novo corte **sobrevivem**,
    com o mesmo tempo absoluto. Os que ficam parcialmente fora são cortados na
    borda, com ponto de zoom interpolado na borda, como no `/edit`.
  - **Confirmação implícita:** o `recut` não tem `confirm_losses` e a tela
    antiga não tem modal. Por isso ele se comporta como `confirm_losses: true`:
    o que fica **totalmente** fora do novo corte é descartado. É o mesmo
    resultado de hoje, mas agora só para esses itens, e não para todos.
  - Os palavrões são recalculados para a nova janela, como no `/edit`.
  - `manual_removed_ranges` também é remapeado, em vez de zerado.
- **O `recut` grava o estilo** (decisão de 06/11). Cada campo de estilo
  **enviado** (`captions_enabled`, `caption_font_size`, `caption_color`,
  `caption_style`, `caption_font_family`) é gravado na coluna
  `caption_style_*` correspondente do Clip. Os campos **omitidos** continuam
  como estão. Sem isso, um `PUT /pan` depois de um `recut` voltaria ao estilo
  do Job (o achado #3 de novo).

## 7. Wireframe da tela de revisão

Na v1 ela entra como a aba **"Revisão (beta)"** dentro do
`ClipEditorScreen`, sem quebrar as abas antigas.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│ ← Voltar   Corte 1 de 3 · 00:04.0 → 00:24.0 (20.0s)    ● Não salvo  [Descartar] [Renderizar] │
├────────────────────────────────────────┬─────────────────────────────────────┤
│ ORIGINAL — prévia ao vivo              │ CORTE RENDERIZADO — versão salva    │
│ ┌────────────────────────────────────┐ │        ┌──────────┐                 │
│ │ vídeo original                     │ │        │  vídeo   │  ⚠ Desatualizado│
│ │      ┌──────┐ ← moldura 9:16 (zoom)│ │        │  9:16    │  há mudanças    │
│ │      │      │                      │ │        │          │  não renderizadas│
│ │      └──────┘                      │ │        └──────────┘                 │
│ │   "Olá pessoal" ← legenda em CSS   │ │                                     │
│ └────────────────────────────────────┘ │                                     │
│ ▶ 00:12.4         [Prévia aproximada]  │                                     │
├────────────────────────────────────────┴─────────────────────────────────────┤
│ Estilo da legenda ▸  [Liga/desliga] [Tamanho] [Cor] [Formato] [Fonte]   (recolhível) │
├──────────────────────────────────────────────────────────────────────────────┤
│              00:00     00:05     00:10     00:15     00:20     00:25           │
│ Corte        ░░░░░░[██████████████████████████████████████]░░░░░               │
│ Legendas            [Olá pessoal][Isso é incrível!]   [Vamos…]                 │
│ Zoom                    ◆               ◆          ◆                           │
│ Removido/Mudo                      ▨▨▨▨       ▒▒                               │
│                              │ ← agulha (posição do player)                    │
└──────────────────────────────────────────────────────────────────────────────┘
```
- A timeline mostra o corte com **30s de folga** de cada lado (60s no total).
  A janela fica fixa na versão salva, para não deslizar durante o arrasto; por
  isso, a cada renderização, o corte pode crescer até 30s de cada lado.
- Em telas estreitas, as duas prévias ficam uma embaixo da outra.

| Trilha | Cor (token do tema) | O que mostra | Interação na v1 |
|---|---|---|---|
| **Corte** | `accent` | faixa entre início e fim; o que está fora fica escurecido | arrastar as alças, com snap (§8) |
| **Legendas** | azul | um bloco por linha; borda sólida = modo `manual`, tracejada = modo `auto`; fora do corte = esmaecida | clicar abre um **editor de legenda próprio, ligado ao rascunho, com o mesmo visual do `CaptionTextEditor` atual** (somente o texto; os tempos não são arrastáveis). Editar o texto passa a legenda para o modo `manual`. O `CaptionTextEditor` atual **não** é reaproveitado porque grava na hora pelo `PUT /captions`, o que quebraria "zero chamadas durante a edição" e "uma renderização por clique" |
| **Zoom** | `warning-marker` (amarelo) | um losango por ponto de enquadramento | só leitura na trilha; o ponto é criado clicando no vídeo, como hoje; some em jobs com tela dividida, com aviso |
| **Removido/Mudo** | `danger` hachurado = removido pelo usuário; cinza com cadeado = filtro de palavrões (só leitura) | trechos que não vão aparecer ou tocar | arrastar na trilha cria um trecho; clicar seleciona; Delete remove (só os do usuário) |

**Diálogo de perdas:**
- É um modal e só aparece **depois** de clicar em Renderizar, se o backend
  responder `409 losses_require_confirmation`. Nunca durante o arrasto.
- Texto: *"N itens ficaram fora do novo corte e serão descartados:"*,
  seguido da lista com ícone, tempo e texto de cada item.
- Botões: **[Voltar e ajustar]** (padrão, o lado seguro) e
  **[Descartar e renderizar]**, que reenvia com `confirm_losses: true`.
- Os ajustes parciais não pedem confirmação. Aparecem no aviso de sucesso.

Durante o arrasto, o frontend só **esmaece** visualmente os itens que estão
fora do corte. É geometria simples, não a regra oficial (que é do backend).

**Estados da tela:**

| Estado | O que o usuário vê |
|---|---|
| Carregando | esqueleto das prévias e das trilhas; `/draft` e `/words` são buscados em paralelo |
| Sem mudanças | "Tudo salvo"; Renderizar desabilitado |
| Editando | "● Não salvo"; Renderizar e Descartar habilitados; prévia da direita marcada como "Desatualizado"; sair da tela pede confirmação |
| Confirmando perdas | modal acima |
| Renderizando | botão "Renderizando… ~10s"; spinner sobre a prévia da direita; timeline só leitura até terminar |
| Sucesso | aviso "Renderizado em 9.8s", com os ajustes, se houver; a prévia da direita recarrega |
| Erro | faixa vermelha com `detail.message`; **o rascunho é preservado**; botão "Tentar de novo" |
| Conflito (`revision_conflict`) | "Este corte foi alterado em outra aba" e [Recarregar]. Na v1 não há mesclagem. |
| Ocupado (`render_in_progress`) | "Outra edição deste corte está renderizando. Tente de novo em alguns segundos." O rascunho é preservado. |

**Estilo na tela antiga:** o `ClipEditorScreen` deixa de iniciar o estilo
com valores fixos (`useState(8)`, `"#FFFFFF"` etc., linhas 85-89) e passa a
ler `clip.caption_style`.

## 8. Snap

- **Tolerância: 300 ms.** Se a fronteira de palavra mais próxima estiver a
  mais de 300 ms, a borda fica onde o usuário soltou, sem forçar.
- **Alt** segurado durante o arrasto desliga o snap. No Mac é o Option, a
  mesma tecla.
- O **início** do corte encaixa no `start` de uma palavra; o **fim**, no
  `end`.
- Na v1, só as alças da trilha **Corte** encaixam. Nada encaixa em cortes
  internos nem no playhead.
- A lógica fica numa função pura em `frontend/src/lib/snap.ts`.
- **Fallback:** se o `GET /words` falhar (rede, 404, 409, ou o backend
  ainda não ter o endpoint) ou vier com `words: []`:
  - o snap fica desligado em silêncio e o arrasto continua livre;
  - aparece um aviso discreto "snap indisponível";
  - tenta uma vez de novo ao reabrir o clipe, sem ficar repetindo;
  - o editor nunca trava por causa disso.
- **Janela pedida:** a área visível da timeline (corte ± 30s).
- **Janela máxima de 600s** no `/words`. Se o pedido passar disso, o
  backend corta para 600s a partir do `from` e responde com `truncated: true`
  e o `to` ajustado. A janela da v1 (clipe de até 60s + 60s de folga) cabe
  numa chamada só. Se um dia a janela passar de 600s, o frontend faz
  chamadas seguidas, começando cada uma no `to` devolvido pela anterior.

## 9. Métricas de sucesso

| # | Métrica | Meta | Como medir |
|---|---|---|---|
| 1 | Edições preservadas ao mudar início ou fim | 100% dos itens que continuam dentro do corte mantêm o tempo absoluto (tolerância de 1 ms) | pytest com ≥ 20 cenários + repetição pela API do cenário do Passo A |
| 2 | Perda silenciosa | 0 **na tela nova** (aba "Revisão (beta)", via `/edit`). Toda remoção passa pelo modal. O `recut` da tela legada descarta sem avisar o que fica totalmente fora (confirmação implícita, §6). | teste da resposta 409 |
| 3 | Renderizações por clique | exatamente 1 | log de chamadas do ffmpeg |
| 4 | Chamadas de API enquanto edita | 0 | aba Rede do navegador |
| 5 | Estilo da legenda | o estilo salvo do clipe é o que aparece no vídeo depois de **qualquer** edição (inclusive mexer no zoom depois de trocar a cor) | pytest + frame extraído do vídeo |
| 6 | Snap | marcador solto cai em fronteira de palavra quando há uma a ≤ 300 ms | teste manual com vídeo em PT |
| 7 | Censura | 0 palavrões audíveis ou legíveis depois de uma edição pelo `/edit`, nos modos `censor` e `cut` | Passo B com vídeo real |
| 8 | Fidelidade da prévia | posição, quebra de linha e cor equivalentes ao renderizado nos 4 formatos; espessura de contorno e sombra pode diferir | comparação lado a lado |
| 9 | Tempo de renderização | no máximo 10% acima do recut atual, para o mesmo clipe | cronômetro (referência: 7–13s num clipe de 17s) |
| 10 | Precisão dos tempos | no máximo 3 casas decimais em toda resposta | testes de contrato |

**Critério para aposentar a tela antiga:**
1. As métricas 1 a 7 passam.
2. O Passo B foi feito com vídeo real em PT.
3. Um job real completo (3 cortes) foi revisado **só na tela nova**.
4. Não há nenhum bug bloqueante aberto.

A remoção da tela antiga vai num commit separado, para poder ser desfeita.

## 10. Divisão de trabalho

Cada agente **só toca os arquivos do seu escopo**. Se precisar de outro,
para e avisa.

**Ordem:** B e C em paralelo (C contra o contrato) → A depois de B →
D depois de A e B → integração e teste de ponta a ponta.

| Agente | Arquivos | Observações |
|---|---|---|
| **B** (modelo e estilo) | `backend/app/models.py` (7 colunas + getters/setters de `manual_removed_ranges`); `backend/app/schemas.py` (**dono**); `backend/app/services/caption_style.py` (**novo**, função pura Clip > Job > default); `backend/app/routers/download.py` (só `_clip_to_out`); `README.md` (seção "Atualizando banco existente") | Não toca em `clip_rendering.py`. **Se mudar o nome de qualquer campo do `ClipOut` ou do `/words` em relação ao contrato, avisa antes de commitar.** |
| **C** (frontend) | `frontend/src/api.ts`, `frontend/src/types.ts`; `frontend/src/components/ReviewTimeline.tsx` (**novo**); `frontend/src/lib/snap.ts` (**novo**); `frontend/src/components/ClipEditorScreen.tsx` | Nova aba "Revisão (beta)". Se o backend ainda não tiver o endpoint, usa mock com `Promise.resolve`, **com os nomes exatos do contrato**. |
| **A** (backend core) | `backend/app/routers/clip_edit.py` (**novo**: `PUT /edit`, `GET /draft`); `backend/app/routers/words.py` (**novo**); `backend/app/services/clip_edit.py` (**novo**: validação, remapeamento, palavrões, censura, compare-and-swap); `backend/app/services/clip_rendering.py` (**dono**: liga o `caption_style.py`, arquivo `_r{n+1}.mp4`, lock por clipe); `backend/app/services/profanity.py` (helper de censura de lista de segmentos); `backend/app/main.py` (`include_router`); `clips.py`, `clip_cuts.py`, `pan.py`, `captions.py` (§6) | Não edita `models.py` nem `schemas.py`; se precisar de um campo, pede ao B. |
| **D** (testes) | `backend/tests/` (**novo**); `backend/requirements-dev.txt` (**novo**, só pytest) | Cobertura mínima: remapeamento (≥ 20 cenários, §4), conversão absoluto ↔ relativo (ida e volta devolve o mesmo valor), compare-and-swap (2 PUTs), `redact_text` só em falas do Whisper, `/words` (ajuste aos limites, `to ≤ from`, janela de 600s com `truncated`, `409 transcription_not_ready`, `404 transcript_not_found`, `censored` e `is_censored`); `recut` preservando as edições que continuam dentro do corte e gravando o estilo enviado. |

**Regras para todos:**
- Comentários em português, explicando o **porquê**.
- Nenhuma biblioteca nova, exceto pytest em `requirements-dev.txt`.
- Uma etapa por vez; ao final, mostrar o diff e rodar a checagem
  (`python -c "import app.main"` no backend, `npx tsc --noEmit` no frontend).

## 11. Fora da v1

- Fila de renderização em segundo plano (responder 202 e avisar quando terminar).
- Arrastar as bordas dos blocos de legenda ou criar legenda nova direto na
  timeline. Na v1, só o texto é editável.
- Criar ou arrastar pontos de zoom direto na trilha.
- Forma de onda do áudio, zoom horizontal da timeline, atalhos de teclado,
  desfazer/refazer.
- Snap do início no processamento automático (hoje só o fim é ajustado).
- Mesclar edições conflitantes entre abas. Na v1, só recarrega.
- Prévia de legenda idêntica ao vídeo final, pixel a pixel.
- Layout otimizado para celular e TV.
- Zoom manual junto com tela dividida (streamer).
- Edição de vários clipes ao mesmo tempo.
- Censura reaplicada nos endpoints antigos.
- Aviso de enquadramento automático nas abas antigas (`recut` e `PUT /cuts`
  não têm diálogo de perdas). Limitação conhecida, a resolver junto com a
  censura dos endpoints antigos.
- Remoção da tela antiga (só depois do critério da §9).

## 12. Riscos conhecidos

1. **Prévia aproximada.** A legenda da esquerda é desenhada pelo navegador,
   não pelo mesmo motor do vídeo final. Para comunicar isso: o selo fixo
   **"Prévia aproximada"** e o vídeo renderizado sempre ao lado.
2. **Clipes longos.** Um clipe de 60s deve levar uns 35–45s para renderizar.
   Na v1 é uma espera com spinner; o ffmpeg roda no processo da API e pode
   deixar outras requisições lentas. Para uma pessoa usando, é aceitável.
3. **Precisão da censura** limitada à do Whisper ao reconhecer a palavra.
4. **Limitações conhecidas da tela legada** (as abas antigas do
   `ClipEditorScreen`, que serão descontinuadas; isso é dito explicitamente
   na apresentação):
   - o `recut` descarta sem avisar o que fica totalmente fora do novo corte
     (§6);
   - ela manda os 5 campos de estilo em todo `recut`, e o `recut` agora os
     grava. A correção (ler `clip.caption_style` em vez dos valores fixos nas
     linhas 85-89) é **obrigatória** no escopo do Agente C. Se não ficar
     pronta a tempo, a tela legada continua sobrescrevendo o estilo do clipe,
     e a demo usa só a aba "Revisão (beta)";
   - os demais comportamentos antigos listados em §11.
