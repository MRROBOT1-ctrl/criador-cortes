"""
Operações de vídeo via FFmpeg (chamado como subprocess — sem moviepy, que
adiciona uma camada Python em cima do ffmpeg sem necessidade aqui).
"""
import json
import logging
import shutil
import subprocess
from pathlib import Path

from app.services import face_detection

logger = logging.getLogger(__name__)


class FFmpegError(RuntimeError):
    pass


def _run(cmd: list[str]) -> None:
    logger.info("Executando: %s", " ".join(cmd))
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"Comando falhou ({' '.join(cmd)}):\n{result.stderr[-4000:]}")


def probe_duration(video_path: Path) -> float:
    """Retorna a duração do vídeo em segundos via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "json", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe falhou: {result.stderr}")
    data = json.loads(result.stdout)
    return float(data["format"]["duration"])


def probe_resolution(video_path: Path) -> tuple[int, int]:
    """Retorna (largura, altura) do vídeo em pixels via ffprobe."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries", "stream=width,height",
        "-of", "json", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe falhou: {result.stderr}")
    stream = json.loads(result.stdout)["streams"][0]
    return int(stream["width"]), int(stream["height"])


def _has_audio_stream(video_path: Path) -> bool:
    """Verifica se o vídeo tem faixa de áudio. Necessário saber ANTES de montar
    o filtro de concat dos cortes internos (`keep_segments`): referenciar
    "0:a" num filtro `atrim` quando o vídeo não tem áudio nenhum QUEBRA o
    ffmpeg ("Stream specifier 'a' in filtergraph ... matches no streams"),
    diferente do "-map 0:a?" de sempre, que tolera a ausência numa boa."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "a",
        "-show_entries", "stream=index", "-of", "json", str(video_path),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        return False
    try:
        return bool(json.loads(result.stdout).get("streams"))
    except (ValueError, KeyError):
        return False


def _is_noop_keep_segments(keep_segments: list[tuple[float, float]] | None, clip_duration: float) -> bool:
    """True quando `keep_segments` não representa nenhum corte interno de
    verdade: None, lista vazia, ou um único segmento cobrindo o clipe
    inteiro (dentro de uma tolerância pequena pra erro de ponto flutuante)."""
    if not keep_segments:
        return True
    if len(keep_segments) == 1:
        s, e = keep_segments[0]
        return s <= 0.01 and e >= clip_duration - 0.01
    return False


def remap_time_through_cuts(t: float, keep_segments: list[tuple[float, float]]) -> float | None:
    """Remapeia um instante `t` no sistema de coordenadas ORIGINAL do clipe
    (antes de remover os trechos internos) pro tempo equivalente DEPOIS do
    corte — soma a duração de cada segmento MANTIDO que vem inteiramente
    antes de `t`. Retorna None se `t` cai dentro de um trecho REMOVIDO (não
    sobra rastro dele no resultado final) — inclusive antes do primeiro
    segmento mantido ou depois do último.

    `keep_segments` precisa estar ordenado e sem overlap (garantido pelo
    validador do endpoint PUT /clips/{id}/cuts).
    """
    kept_before = 0.0
    for seg_start, seg_end in keep_segments:
        if t < seg_start:
            return None  # buraco removido antes desse segmento (ou antes do primeiro de todos)
        if t <= seg_end:
            return kept_before + (t - seg_start)
        kept_before += seg_end - seg_start
    return None  # depois do último segmento mantido — também removido


def remap_range_through_cuts(
    seg_start: float, seg_end: float, keep_segments: list[tuple[float, float]]
) -> tuple[float, float] | None:
    """Remapeia um intervalo [seg_start, seg_end) — tipicamente uma linha de
    legenda — pro sistema pós-corte, usando `remap_time_through_cuts`.

    Decisão de design (documentada aqui por ser importante pra quem for
    mexer depois): se o INÍCIO do intervalo já cai num trecho removido, a
    linha inteira é DESCARTADA (retorna None) — preferimos perder uma
    legenda inteira a arriscar ela "pular" de um trecho do vídeo pro outro
    sem relação nenhuma com o que está sendo dito naquele momento. Já se só
    o FIM cai num buraco (a fala atravessa a borda de um corte), a linha é
    TRUNCADA no fim do segmento mantido em que ela começou — geralmente
    melhor mostrar a legenda um pouco menos tempo do que sumir com ela por
    completo quando a maior parte da fala sobreviveu ao corte.
    """
    new_start = remap_time_through_cuts(seg_start, keep_segments)
    if new_start is None:
        return None

    new_end = remap_time_through_cuts(seg_end, keep_segments)
    if new_end is None:
        # Atravessa a borda de um corte: trunca no fim do segmento mantido
        # em que a linha começou.
        for s0, s1 in keep_segments:
            if s0 <= seg_start <= s1:
                new_end = remap_time_through_cuts(s1, keep_segments)
                break

    if new_end is None or new_end <= new_start:
        return None
    return new_start, new_end


def extract_audio(video_path: Path, output_wav: Path) -> None:
    """Extrai o áudio em WAV mono 16kHz — formato esperado pelo Whisper."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        str(output_wav),
    ]
    _run(cmd)


def _escape_concat_list_path(path: Path) -> str:
    """Escapa um caminho pra entrar numa linha `file '...'` do demuxer concat
    do ffmpeg — a sintaxe dele é a mesma de um shell POSIX simples: uma aspa
    simples dentro do caminho precisa virar `'\\''` (fecha a aspa, escapa uma
    aspa literal, reabre a aspa)."""
    return str(path).replace("'", "'\\''")


def _concat_via_demuxer(clip_paths: list[Path], list_file: Path, output_path: Path) -> None:
    """Concatena sem reencodar (`-c copy`) via demuxer concat do ffmpeg —
    rápido e sem perda de qualidade, mas só funciona se TODOS os clipes
    tiverem exatamente os mesmos parâmetros de codec/timebase (é o caso aqui,
    já que todo clipe sai do mesmo `cut_and_render_vertical` acima). Levanta
    FFmpegError se o ffmpeg falhar — o chamador decide se cai pro fallback
    com reencode."""
    list_file.write_text(
        "\n".join(f"file '{_escape_concat_list_path(p)}'" for p in clip_paths) + "\n",
        encoding="utf-8",
    )
    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file), "-c", "copy", str(output_path),
    ]
    _run(cmd)


def _concat_via_filter_reencode(clip_paths: list[Path], output_path: Path) -> None:
    """Fallback pra quando o `-c copy` não funciona (parâmetros de codec
    divergentes entre os clipes — ex: um com watermark/legenda e outro sem
    pode ainda assim divergir em detalhes do stream de áudio/vídeo). Mais
    lento porque reencoda tudo, mas funciona mesmo com entradas heterogêneas
    já que o filtro `concat` decodifica cada uma antes de juntar."""
    cmd = ["ffmpeg", "-y"]
    for p in clip_paths:
        cmd += ["-i", str(p)]

    n = len(clip_paths)
    filter_inputs = "".join(f"[{i}:v:0][{i}:a:0]" for i in range(n))
    cmd += [
        "-filter_complex", f"{filter_inputs}concat=n={n}:v=1:a=1[outv][outa]",
        "-map", "[outv]", "-map", "[outa]",
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    _run(cmd)


def concat_clips(clip_paths: list[Path], output_path: Path, tmp_dir: Path) -> None:
    """Junta vários clipes já renderizados (mesma ordem da lista) num único
    arquivo de saída.

    Tenta primeiro o concat "burro" via demuxer (`-c copy`, sem reencode —
    todos os clipes já saem de `cut_and_render_vertical` com o mesmo
    1080x1920/30fps/libx264/aac, então na imensa maioria dos casos os
    parâmetros batem e isso é quase instantâneo). Se o ffmpeg falhar OU o
    resultado sair com duração muito diferente da soma dos clipes de entrada
    (sintoma comum de concat "silenciosamente quebrado" — ex: só o primeiro
    clipe fica no arquivo final quando os parâmetros divergem sutilmente),
    descarta e cai pro fallback com reencode via filtro `concat`, que sempre
    funciona custando mais tempo de processamento.

    `output_path` só é sobrescrito no final, com troca atômica — uma falha
    no meio do processo não deve corromper um compilado anterior válido.
    """
    if not clip_paths:
        raise ValueError("Lista de clipes vazia — nada para concatenar.")

    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_output = output_path.with_suffix(".compile_tmp.mp4")
    list_file = tmp_dir / "concat_list.txt"

    # Caso trivial: um clipe só não precisa de concat nenhum, só copiar.
    if len(clip_paths) == 1:
        shutil.copyfile(clip_paths[0], tmp_output)
        tmp_output.replace(output_path)
        list_file.unlink(missing_ok=True)
        return

    expected_duration = sum(probe_duration(p) for p in clip_paths)
    # Tolerância de 1.5s pro somatório: cada clipe individualmente já pode
    # ter uma fração de segundo de folga por causa de arredondamento de
    # frames no corte original — soma alguns clipes e essa folga cresce.
    duration_tolerance = max(1.5, 0.05 * expected_duration)

    copy_ok = False
    try:
        _concat_via_demuxer(clip_paths, list_file, tmp_output)
        result_duration = probe_duration(tmp_output)
        copy_ok = abs(result_duration - expected_duration) <= duration_tolerance
        if not copy_ok:
            logger.warning(
                "Concat via -c copy saiu com duração %.2fs (esperado %.2fs) — "
                "parâmetros de codec provavelmente divergem entre os clipes, "
                "caindo pro fallback com reencode.",
                result_duration, expected_duration,
            )
    except FFmpegError as exc:
        logger.warning("Concat via -c copy falhou (%s) — caindo pro fallback com reencode.", exc)
        copy_ok = False

    if not copy_ok:
        tmp_output.unlink(missing_ok=True)
        _concat_via_filter_reencode(clip_paths, tmp_output)

    list_file.unlink(missing_ok=True)
    tmp_output.replace(output_path)  # troca atômica, só após sucesso de um dos dois caminhos


WATERMARK_WIDTH_PX = 180  # largura fixa da logo depois de escalada, mantendo proporção
WATERMARK_MARGIN_PX = 24

# (x, y) da logo já escalada dentro do frame 1080x1920 — "w"/"h" são a
# largura/altura da logo depois do overlay escalar, "W"/"H" o frame inteiro
# (variáveis do próprio ffmpeg no filtro overlay).
_WATERMARK_POSITIONS = {
    "top-left": (f"{WATERMARK_MARGIN_PX}", f"{WATERMARK_MARGIN_PX}"),
    "top-right": (f"W-w-{WATERMARK_MARGIN_PX}", f"{WATERMARK_MARGIN_PX}"),
    "bottom-left": (f"{WATERMARK_MARGIN_PX}", f"H-h-{WATERMARK_MARGIN_PX}"),
    "bottom-right": (f"W-w-{WATERMARK_MARGIN_PX}", f"H-h-{WATERMARK_MARGIN_PX}"),
}

# Presets de estilo de legenda. A posição vertical (MarginV) fica FIXA por
# preset e não é exposta ao usuário: testado empiricamente que valores de
# MarginV muito altos (ex: 340) empurram a legenda pra FORA da tela inteira
# em vez de só subi-la (o libass não escala esse valor do jeito "óbvio" numa
# imagem 1080x1920) — os valores abaixo já foram validados visualmente (frame
# extraído de um render real).
#   bold_outline: o estilo "viral" já existente antes desta feature —
#       fonte grande em negrito, só contorno grosso + sombra, sem caixa.
#   boxed: legenda clássica, caixa preta sólida atrás do texto
#       (BorderStyle=3 = "opaque box" no libass).
#   minimal: contorno fino, sem negrito, mais discreto.
#   no_outline: sem contorno nem sombra nenhuma — só o texto em negrito,
#       "flutuando" sobre o vídeo (pedido explícito do usuário: "sem borda").
CAPTION_STYLE_PRESETS: dict[str, dict[str, int]] = {
    "bold_outline": {"border_style": 1, "outline": 4, "shadow": 1, "bold": 1, "margin_v": 140},
    "boxed": {"border_style": 3, "outline": 2, "shadow": 0, "bold": 1, "margin_v": 80},
    "minimal": {"border_style": 1, "outline": 2, "shadow": 0, "bold": 0, "margin_v": 140},
    "no_outline": {"border_style": 1, "outline": 0, "shadow": 0, "bold": 1, "margin_v": 140},
}


def _hex_to_ass_color(hex_color: str) -> str:
    """Converte uma cor hex '#RRGGBB' pro formato de cor do ASS/libass
    '&H00BBGGRR' — o ASS usa a ordem BGR (invertida da RGB usual), com o
    primeiro byte "00" sendo o canal alpha (00 = totalmente opaco)."""
    hex_color = hex_color.lstrip("#")
    r, g, b = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    return f"&H00{b}{g}{r}".upper()


def _build_pan_crop_x_expr(samples: list[tuple[float, float]], scaled_w: int, crop_w: int = 1080) -> str:
    """Constrói uma expressão do ffmpeg (usada no `x=` do filtro `crop`) que
    interpola linearmente entre as amostras de `track_horizontal_pan`,
    convertendo posição normalizada (0..1) em offset de crop em pixels já
    limitado a [0, scaled_w - crop_w] (nunca deixa o crop passar da borda).

    Construída de trás pra frente: cada nível é um `if(lt(t, t_próximo), ...)`
    que interpola até o próximo ponto conhecido, caindo pro nível anterior
    (mais recente na amostragem) caso contrário — resultado final é uma única
    expressão aninhada, sem precisar do `sendcmd`/parâmetros dinâmicos do
    ffmpeg (mais simples de montar e de depurar).
    """
    max_x = max(0, scaled_w - crop_w)

    def to_crop_x(norm_cx: float) -> float:
        x = norm_cx * scaled_w - crop_w / 2
        return max(0.0, min(float(max_x), x))

    points = [(t, to_crop_x(cx)) for t, cx in samples]
    expr = f"{points[-1][1]:.1f}"
    for i in range(len(points) - 2, -1, -1):
        t0, x0 = points[i]
        t1, x1 = points[i + 1]
        expr = f"if(lt(t,{t1:.3f}),{x0:.1f}+({x1:.1f}-{x0:.1f})*(t-{t0:.3f})/{(t1 - t0):.6f},{expr})"
    return expr


# Altura (em pixels, no canvas final 1080x1920) reservada pra faixa de cima
# quando há uma janela de streamer detectada — o resto (1920 - isso) vai pro
# gameplay embaixo. ~36% pro streamer dá espaço suficiente pro rosto sem
# sacrificar demais o jogo (validado visualmente, ver services/face_detection.py).
STREAMER_TOP_HEIGHT_PX = 700


def cut_and_render_vertical(
    video_path: Path,
    start: float,
    end: float,
    output_path: Path,
    srt_path: Path | None = None,
    watermark_path: Path | None = None,
    watermark_position: str = "bottom-right",
    captions_enabled: bool = True,
    caption_font_size: int = 20,
    caption_color: str = "#FFFFFF",
    caption_style: str = "bold_outline",
    caption_font_family: str = "DejaVu Sans",
    streamer_region: tuple[int, int, int, int] | None = None,
    manual_pan_track: list[tuple[float, float]] | None = None,
    keep_segments: list[tuple[float, float]] | None = None,
) -> None:
    """Corta o trecho [start, end] do vídeo e renderiza em 1080x1920 (9:16),
    30fps, com legenda queimada (se fornecida), marca d'água/logo (se
    fornecida) e layout de tela dividida (se `streamer_region` fornecido —
    ver app/services/face_detection.py).

    Estratégia de enquadramento padrão (sem `streamer_region`): escala
    mantendo proporção pela altura e corta o excesso nas laterais (crop
    central) — funciona bem pra vídeo landscape virando vertical sem
    distorcer a imagem. Com `streamer_region` (x, y, w, h em pixels da
    resolução ORIGINAL do vídeo), monta tela dividida: a região indicada
    (webcam do streamer) ocupa a faixa de cima, o crop central de sempre
    ocupa a faixa de baixo.

    Sem `streamer_region`, ainda tenta um crop horizontal DINÂMICO que
    acompanha o rosto (`manual_pan_track` se fornecido pelo usuário, senão
    `face_detection.track_horizontal_pan` automático) — cai pro crop central
    estático se nenhum dos dois achar nada confiável.

    `keep_segments`: lista de (início, fim) em segundos relativos a `start`
    (0 = `start`) dos trechos do MEIO do clipe que devem sobreviver — usado
    pra remover um pedaço qualquer (silêncio, fala descartada) sem deixar
    buraco no resultado final. Quando representa mais de um pedaço (ou um
    pedaço só que não cobre o clipe inteiro), o vídeo original é recortado
    em cada sub-trecho e concatenado (filtro `concat` do ffmpeg) ANTES de
    qualquer outro estágio do pipeline — o resto (scale/crop/pan, legenda,
    watermark) roda em cima do resultado já concatenado, sem saber que
    existiu um corte no meio. `None` ou um segmento cobrindo o clipe inteiro
    == comportamento de sempre (sem cortes internos).
    """
    duration = end - start
    has_internal_cuts = not _is_noop_keep_segments(keep_segments, duration)
    if has_internal_cuts:
        # A duração final não é mais `end - start`: é a soma só dos pedaços
        # mantidos (os buracos removidos não contam). `-t` no fim do comando
        # usa esse valor — um pouco de folga (poucos ms) evita que arredonda-
        # mento de ponto flutuante corte o último frame do último pedaço.
        duration = sum(seg_end - seg_start for seg_start, seg_end in keep_segments)
    has_watermark = watermark_path is not None and watermark_path.exists()
    # Um .srt vazio (0 bytes) acontece quando o trecho não tem fala detectada
    # (ex: só música/efeitos) — nesse caso o filtro "subtitles" do ffmpeg/libass
    # falha com "Unable to open" em vez de simplesmente não legendar, então só
    # aplica o filtro se o arquivo realmente tiver conteúdo. `captions_enabled`
    # permite desligar a legenda de propósito mesmo com um .srt válido em mãos
    # (opção "sem legenda" pedida pelo usuário).
    has_subtitles = (
        captions_enabled and srt_path is not None and srt_path.exists() and srt_path.stat().st_size > 0
    )

    # Tudo passa por -filter_complex (mesmo os casos simples, sem watermark/
    # streamer) — evita ter dois caminhos de código (um com -vf, outro com
    # -filter_complex) fazendo basicamente a mesma coisa; filter_complex é um
    # superconjunto de -vf, então não muda o resultado do caso simples.
    stages: list[str] = []

    # `video_src`/`audio_map` apontam pro que o resto do pipeline deve usar
    # como "o vídeo/áudio de entrada" — normalmente o próprio input 0, mas
    # quando há cortes internos (`keep_segments`) viram a saída do concat
    # montado abaixo. Só existe UM lugar (aqui) que decide isso: o resto da
    # função nem precisa saber que existiu um corte no meio.
    video_src = "0:v"
    audio_map = "0:a?"

    if has_internal_cuts:
        # `-ss start` (mais abaixo, no comando) já reposiciona o timestamp 0
        # do stream decodificado pro início do CLIPE — por isso os tempos de
        # `keep_segments` (relativos ao início do clipe) podem ser usados
        # DIRETO como start/end do `trim`/`atrim`, sem somar `start` de novo
        # (mesma convenção já usada pelo `manual_pan_track`/legenda no resto
        # do arquivo).
        has_audio = _has_audio_stream(video_path)
        concat_inputs: list[str] = []
        for i, (seg_start, seg_end) in enumerate(keep_segments):
            stages.append(f"[0:v]trim=start={seg_start}:end={seg_end},setpts=PTS-STARTPTS[vcut{i}]")
            concat_inputs.append(f"[vcut{i}]")
            if has_audio:
                stages.append(f"[0:a]atrim=start={seg_start}:end={seg_end},asetpts=PTS-STARTPTS[acut{i}]")
                concat_inputs.append(f"[acut{i}]")
        n = len(keep_segments)
        if has_audio:
            stages.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=1[vcat][acat]")
            audio_map = "[acat]"
        else:
            stages.append(f"{''.join(concat_inputs)}concat=n={n}:v=1:a=0[vcat]")
        video_src = "vcat"

    def _subtitles_filter() -> str:
        # ffmpeg exige escapar ':' no path do filtro subtitles no Linux
        escaped = str(srt_path).replace(":", "\\:")
        # `caption_font_family` precisa ser uma fonte que exista de fato na
        # imagem Docker (ver Dockerfile + schemas.CAPTION_FONT_FAMILIES) —
        # uma fonte não instalada cai num fallback silencioso do fontconfig
        # em vez de erro, então a validação de allowlist acontece no router.
        # Posição (MarginV) fixa por preset — ver CAPTION_STYLE_PRESETS acima
        # pro motivo de não deixar isso livre pro usuário configurar.
        preset = CAPTION_STYLE_PRESETS.get(caption_style, CAPTION_STYLE_PRESETS["bold_outline"])
        primary_colour = _hex_to_ass_color(caption_color)
        style = (
            f"FontName={caption_font_family},Bold={preset['bold']},FontSize={caption_font_size},"
            f"PrimaryColour={primary_colour},"
            "OutlineColour=&H00000000,BackColour=&H00000000,"
            f"BorderStyle={preset['border_style']},Outline={preset['outline']},"
            f"Shadow={preset['shadow']},Alignment=2,MarginV={preset['margin_v']}"
        )
        return f"subtitles='{escaped}':force_style='{style}'"

    if streamer_region is not None:
        sx, sy, sw, sh = streamer_region
        bottom_h = 1920 - STREAMER_TOP_HEIGHT_PX
        # `video_src` é consumido DUAS vezes aqui (recorte do streamer e
        # recorte do gameplay) — usar o mesmo label do ffmpeg duas vezes como
        # entrada de filtros diferentes SEM um `split` explícito é uma
        # armadilha real: pra um input bruto do demuxer (tipo "0:v") o ffmpeg
        # duplica sozinho sem problema, mas pra saída de OUTRO filtro (como o
        # "vcat" do concat dos cortes internos) o segundo consumidor pode
        # silenciosamente "perder" o efeito da cadeia anterior — sem erro
        # nenhum, só o resultado errado (confirmado testando: o segundo
        # consumidor lia o vídeo como se os cortes internos não tivessem
        # acontecido). `split` resolve duplicando o stream de forma explícita
        # — inofensivo mesmo quando `video_src` já era "0:v".
        stages.append(f"[{video_src}]split=2[{video_src}_cam][{video_src}_game]")
        stages.append(
            f"[{video_src}_cam]crop={sw}:{sh}:{sx}:{sy},"
            f"scale=1080:{STREAMER_TOP_HEIGHT_PX}:force_original_aspect_ratio=increase,"
            f"crop=1080:{STREAMER_TOP_HEIGHT_PX}[camcrop]"
        )
        stages.append(
            f"[{video_src}_game]scale=1080:{bottom_h}:force_original_aspect_ratio=increase,"
            f"crop=1080:{bottom_h}[gamecrop]"
        )
        # A legenda é queimada AQUI, na fatia do gameplay (1080x{bottom_h}),
        # ANTES do vstack com o streamer — não no canvas final já empilhado.
        # Isso confina a legenda fisicamente à fatia de baixo (o recorte do
        # próprio ffmpeg vira o limite: texto não pode "vazar" pra cima da
        # divisória por mais linhas/fonte grande que tenha), em vez de só
        # empurrá-la com MarginV a partir do fundo do frame INTEIRO (1920px)
        # sem noção nenhuma de onde fica a linha streamer/gameplay — era
        # assim antes e um texto longo com fonte grande cobria boa parte do
        # streamer (confirmado visualmente num teste sintético de pior caso).
        gamecrop_stream = "gamecrop"
        if has_subtitles:
            stages.append(f"[gamecrop]{_subtitles_filter()}[gamecrop_sub]")
            gamecrop_stream = "gamecrop_sub"
        stages.append(f"[camcrop][{gamecrop_stream}]vstack=inputs=2[vbase]")
        current = "vbase"
    else:
        # Vídeo solo (sem streamer_region): tenta um "zoom automático em quem
        # fala" — rastreia a posição horizontal do rosto ao longo do PRÓPRIO
        # trecho sendo cortado e move o crop pra acompanhar (a pessoa se
        # inclina/anda e o corte fixo de sempre podia cortar ela fora).
        # Best-effort, mesmo espírito do streamer_region: se não achar rosto
        # de forma confiável, cai pro crop central estático de sempre —
        # nunca expõe toggle nem quebra o job por causa disso.
        pan_expr = None
        try:
            orig_w, orig_h = probe_resolution(video_path)
            # Mesma conta que "scale=1080:1920:force_original_aspect_ratio=
            # increase" faria: escalar pela altura só compensa (cobre a
            # largura toda) quando o resultado já sai mais largo que 1080 —
            # senão o vídeo é mais "vertical" que 9:16 e não sobra largura
            # nenhuma pra acompanhar ninguém (cai pro estático de qualquer jeito).
            scaled_w = round(orig_w * 1920 / orig_h / 2) * 2
            if scaled_w > 1080:
                # `manual_pan_track` (definido pelo usuário no editor) tem
                # prioridade sobre a detecção automática — ver Clip.manual_pan_keyframes.
                # Espera-se que já venha remapeado pro sistema PÓS-CORTE (ver
                # remap_time_through_cuts) quando `keep_segments` está ativo —
                # essa função só consome o rastro, não sabe nada de cortes.
                if manual_pan_track is not None:
                    pan_track = manual_pan_track
                elif not has_internal_cuts:
                    pan_track = face_detection.track_horizontal_pan(video_path, start, end)
                else:
                    # Detecção automática amostra o trecho ORIGINAL inteiro
                    # (com os buracos ainda presentes) e não tem como saber
                    # quais pedaços vão sumir — os tempos resultantes não
                    # bateriam com a linha do tempo já concatenada. Sem um
                    # pan manual (já remapeado) pra usar no lugar, mais seguro
                    # cair pro crop central estático do que aplicar um pan
                    # deslocado. Limitação conhecida: auto-pan não funciona
                    # junto com cortes internos, só o manual.
                    pan_track = None
                if pan_track:
                    pan_expr = _build_pan_crop_x_expr(pan_track, scaled_w)
        except Exception:  # noqa: BLE001 — é um "extra", nunca deve derrubar o corte
            logger.exception("Falha no rastreamento de pan (%s) — usando crop central.", video_path)

        if pan_expr is not None:
            stages.append(f"[{video_src}]scale={scaled_w}:1920[vscaled]")
            stages.append(f"[vscaled]crop=1080:1920:x='{pan_expr}':y=0[vbase]")
        else:
            stages.append(f"[{video_src}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[vbase]")
        current = "vbase"
        if has_subtitles:
            stages.append(f"[{current}]{_subtitles_filter()}[vsub]")
            current = "vsub"

    # IMPORTANTE: "-i watermark_path" (quando existe) precisa vir DEPOIS do
    # "-i video_path" mas ANTES de qualquer opção de saída como "-t" — opções
    # tipo -ss/-t colocadas antes de um "-i" se associam a ESSE input, não ao
    # anterior (bug real encontrado testando marca d'água: um "-t" mal
    # posicionado entre os dois "-i" fazia o vídeo sair sem corte de duração
    # nenhum, porque o "-t" acabava aplicado à imagem da logo). Por isso o
    # "-t" só entra no fim do comando, como opção de saída, depois de TODOS
    # os "-i".
    cmd = ["ffmpeg", "-y", "-ss", str(start), "-i", str(video_path)]

    if has_watermark:
        cmd += ["-i", str(watermark_path)]
        x, y = _WATERMARK_POSITIONS.get(watermark_position, _WATERMARK_POSITIONS["bottom-right"])
        stages.append(f"[1:v]scale={WATERMARK_WIDTH_PX}:-1[wm]")
        stages.append(f"[{current}][wm]overlay={x}:{y}[vout]")
        current = "vout"

    cmd += ["-filter_complex", ";".join(stages), "-map", f"[{current}]", "-map", audio_map]

    # Com cortes internos, `duration` já é a soma exata dos pedaços mantidos
    # (o concat não produz nada além disso) — uma folga pequena evita que
    # arredondamento de ponto flutuante corte o últimíssimo frame por engano;
    # sem cortes internos, mantém o "-t" exato de sempre (end - start).
    output_duration = duration + 0.05 if has_internal_cuts else duration

    cmd += [
        "-t", str(output_duration),
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    _run(cmd)
