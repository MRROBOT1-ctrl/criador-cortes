## PASSO A PASSO DO PROCESSAMENTO

### FASE 1: PRÉ-PROCESSAMENTO
1. Upload do vídeo para pasta uploads/{job_id}/
2. Extração do áudio (formato WAV, 16kHz)
3. Geração de thumbnails do vídeo

### FASE 2: TRANSCRIÇÃO
1. Executar Whisper no áudio extraído
2. Gerar arquivo .srt com timestamps precisos
3. Armazenar transcrição completa em JSON
4. Identificar automaticamente o idioma

### FASE 3: ANÁLISE DE CONTEÚDO
1. **Análise de Áudio:**
   - Detectar picos de volume (emoção)
   - Detectar mudanças de tom (entusiasmo)
   - Analisar sentimento por segmento (positivo/negativo)
   - Identificar risadas, gritos, silêncios

2. **Análise Visual (opcional):**
   - Detectar mudanças de cena
   - Identificar pessoas/faces
   - Medir movimento na tela

3. **Análise de Texto:**
   - Extrair palavras-chave importantes
   - Detectar perguntas interessantes
   - Identificar momentos de storytelling
   - Reconhecer nomes próprios

### FASE 4: PONTUAÇÃO DOS MOMENTOS
Cada segmento de 5 segundos recebe uma nota de 0 a 100 baseado em:
- 30% → Pico emocional (volume + tom)
- 25% → Palavras-chave relevantes
- 20% → Movimento/mudança de cena
- 15% → Sentimento positivo
- 10% → Duração ideal (não muito curto/longo)

### FASE 5: SELEÇÃO E CORTE
1. Ordenar segmentos por pontuação
2. Selecionar Top 10 momentos (evitando sobreposição)
3. Para cada momento, criar um corte de X segundos
4. Aplicar zoom (se rosto detectado)
5. Adicionar legendas do .srt sincronizadas

### FASE 6: EXPORTAÇÃO
1. Renderizar cada corte em 1080x1920
2. Aplicar formatação de legenda (fonte, cor, tamanho)
3. Adicionar bordas/gradientes
4. Salvar na pasta outputs/{job_id}/
5. Gerar arquivo ZIP para download