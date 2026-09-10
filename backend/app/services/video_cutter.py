"""
Operações de vídeo via FFmpeg (chamado como subprocess — sem moviepy, que
adiciona uma camada Python em cima do ffmpeg sem necessidade aqui).
"""
import json
import logging
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


def extract_audio(video_path: Path, output_wav: Path) -> None:
    """Extrai o áudio em WAV mono 16kHz — formato esperado pelo Whisper."""
    cmd = [
        "ffmpeg", "-y", "-i", str(video_path),
        "-vn", "-ac", "1", "-ar", "16000",
        str(output_wav),
    ]
    _run(cmd)


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
    """
    duration = end - start
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
        stages.append(
            f"[0:v]crop={sw}:{sh}:{sx}:{sy},"
            f"scale=1080:{STREAMER_TOP_HEIGHT_PX}:force_original_aspect_ratio=increase,"
            f"crop=1080:{STREAMER_TOP_HEIGHT_PX}[camcrop]"
        )
        stages.append(
            f"[0:v]scale=1080:{bottom_h}:force_original_aspect_ratio=increase,"
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
                pan_track = face_detection.track_horizontal_pan(video_path, start, end)
                if pan_track is not None:
                    pan_expr = _build_pan_crop_x_expr(pan_track, scaled_w)
        except Exception:  # noqa: BLE001 — é um "extra", nunca deve derrubar o corte
            logger.exception("Falha no rastreamento de pan (%s) — usando crop central.", video_path)

        if pan_expr is not None:
            stages.append(f"[0:v]scale={scaled_w}:1920[vscaled]")
            stages.append(f"[vscaled]crop=1080:1920:x='{pan_expr}':y=0[vbase]")
        else:
            stages.append("[0:v]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920[vbase]")
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

    cmd += ["-filter_complex", ";".join(stages), "-map", f"[{current}]", "-map", "0:a?"]

    cmd += [
        "-t", str(duration),
        "-r", "30",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        str(output_path),
    ]
    _run(cmd)
