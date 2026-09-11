## FUNCIONALIDADES OBRIGATÓRIAS

1. **Upload de Vídeo**
   - Aceitar formatos: MP4, MOV, AVI, MKV
   - Tamanho máximo: 5GB
   - Indicador de progresso do upload

2. **Análise Automática**
   - Transcrição do áudio (Whisper ou similar)
   - Detecção de picos de emoção (análise de tom de voz)
   - Detecção de palavras-chave/configuraveis pelo usuário
   - Detecção de momentos de alto movimento/energia visual
   - Identificação automática dos 5-10 melhores momentos

3. **Geração de Cortes**
   - Cada corte com duração: 15-60 segundos (configurável)
   - Formato vertical: 1080x1920 (9:16)
   - Legendas automáticas com sincronia
   - Efeito de zoom automático em quem está falando (opcional)
   - Transições suaves entre cortes

4. **Pré-visualização**
   - Player para assistir cada corte antes de exportar
   - Opção de refazer corte manualmente
   - Ajuste de tempo de início/fim

5. **Exportação**
   - Vídeo em 1080x1920, 30fps
   - Legendas "hardcoded" (queimadas no vídeo)
   - Tamanho otimizado para redes sociais
   - Download em ZIP com todos os cortes

6. **Interface Web**
   - Design moderno e intuitivo
   - Dark mode por padrão
   - Feedback visual em tempo real
   - Painel de configurações avançadas

## FUNCIONALIDADES DESEJÁVEIS (FUTURO)

- Detecção automática de capítulos/segmentos
- Geração de títulos sugestivos para cada corte
- Thumbnail automática
- Agendamento para publicação direta no TikTok/Instagram
- Suporte a múltiplos idiomas
- Filtro de "palavrões" para cortar ou censurar

---

## Status de implementação (atualizado 2026-09-11)

Anotação adicionada depois — o conteúdo acima é o spec original do DeepSeek e
não foi alterado (ver README.md, seção "Como rodar", sobre esses arquivos
`deepseek_*` ficarem como referência histórica).

- ✅ **Geração de títulos sugestivos para cada corte** — implementado
  (`backend/app/services/titles.py`). Mesmo espírito/limitação das hashtags
  já existentes: template + palavra-chave extraída da transcrição, **não** é
  geração via modelo de linguagem. Editável pelo usuário antes de publicar.
- ✅ **Filtro de "palavrões" para cortar ou censurar** — implementado
  (`backend/app/services/profanity.py`, `Job.profanity_filter`). Dois modos:
  "cut" remove o trecho da palavra do vídeo (reaproveita o mecanismo de
  cortes internos), "censor" muta só o áudio da palavra + troca por
  asteriscos na legenda. Lista de palavras fixa (PT-BR), sem flexão
  automática — best-effort, não pega tudo.
- ❌ Detecção automática de capítulos/segmentos — não implementado
- ❌ Thumbnail automática — não implementado
- ❌ Agendamento para publicação direta no TikTok/Instagram — não
  implementado (precisa de credenciais reais de app nas duas plataformas,
  incluindo app review do TikTok — combinar antes de começar)
- ⚠️ Suporte a múltiplos idiomas — a TRANSCRIÇÃO autodetecta o idioma
  (Whisper), mas a interface em si só existe em português

Ver README.md ("Status atual — MVP backend") para o checklist completo do
resto do projeto.