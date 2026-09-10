"""
Detecção da "janela do streamer" (webcam overlay) dentro do vídeo original,
pra montar um layout de tela dividida (streamer em cima, gameplay embaixo)
em vez do crop central simples.

Abordagem (best-effort, sem modelo pesado — Haar cascade do próprio OpenCV):
amostra alguns frames espalhados pelo vídeo, roda detecção de rosto em cada
um, e só considera "achou a webcam" se um rosto aparecer repetidamente perto
da MESMA quina da tela — webcams de streamer ficam paradas num canto fixo
(overlay do OBS/software de stream), diferente de um rosto que porventura
apareça dentro do próprio jogo (esses tendem a se mover/sumir entre frames).
Se não achar esse padrão estável, retorna None e o pipeline segue com o
crop central de sempre.

IMPORTANTE: os frames de amostra são extraídos via `ffmpeg` (subprocess),
NÃO via `cv2.VideoCapture` direto no vídeo. Testado na prática: vídeos
baixados via yt-dlp costumam vir em AV1, e o backend de vídeo do
opencv-python(-headless) não decodifica AV1 direito (fica com "Failed to
get pixel format"/"Missing Sequence Header" e só retorna frames quebrados,
fazendo a detecção falhar silenciosamente mesmo com uma webcam bem visível
no vídeo). O ffmpeg do projeto já decodifica esses vídeos corretamente (é o
mesmo usado em `video_cutter.py`), então reaproveitamos ele só pra extrair
frames como imagem estática (.jpg) — cv2 entra só pra rodar a detecção de
rosto em cima da imagem já decodificada, sem tocar no vídeo.
"""
import logging
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

import cv2

logger = logging.getLogger(__name__)

_FACE_CASCADE_PATH = cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
_face_cascade: cv2.CascadeClassifier | None = None

# Tenta do mais rigoroso pro mais tolerante, usando o primeiro nível que
# achar uma região estável o bastante (ver _pick_region). Motivo de não usar
# um valor fixo só: testado empiricamente contra 2 vídeos reais diferentes —
# minNeighbors=12 elimina de vez um falso positivo muito estável (o minimap
# circular de HUD de jogos tipo Red Dead Redemption 2 engana o cascade de
# rosto padrão), mas em outro vídeo real a webcam era mais difícil de
# detectar (ângulo/luz/tamanho) e só batia 1x em 14 amostras com
# minNeighbors=12 — nível fixo alto perdia essa webcam por completo. Começar
# estrito e só relaxar se não achar nada dá o melhor dos dois: evita o
# falso positivo fácil quando o rosto real é fácil de achar, e ainda pega
# webcams mais difíceis quando precisa.
_MIN_NEIGHBORS_LEVELS = (12, 9, 6)


def _get_cascade() -> cv2.CascadeClassifier:
    global _face_cascade
    if _face_cascade is None:
        _face_cascade = cv2.CascadeClassifier(_FACE_CASCADE_PATH)
    return _face_cascade


def _extract_frame(video_path: Path, timestamp: float, dest: Path) -> bool:
    """Extrai um frame no timestamp dado via ffmpeg. Retorna False se falhar
    (ex: timestamp além do fim do vídeo) em vez de levantar exceção — é
    esperado que algumas amostras falhem sem invalidar as outras."""
    cmd = [
        "ffmpeg", "-y", "-ss", str(timestamp), "-i", str(video_path),
        "-frames:v", "1", "-q:v", "3", str(dest),
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return result.returncode == 0 and dest.exists() and dest.stat().st_size > 0


def _quadrant(box: tuple[int, int, int, int], frame_w: int, frame_h: int) -> tuple[bool, bool]:
    x, y, w, h = box
    cx, cy = x + w / 2, y + h / 2
    return (cx < frame_w / 2, cy < frame_h / 2)


def _pick_region(
    detections: list[tuple[int, int, int, int]],
    frame_w: int,
    frame_h: int,
    sample_count: int,
) -> tuple[int, int, int, int] | None:
    """Decide se as detecções (já filtradas pra perto de uma quina) formam
    uma região confiável o bastante — e se sim, retorna a caixa expandida.

    Limiar deliberadamente tolerante: um streamer real vira o rosto, olha
    pra baixo, sai de quadro etc. em boa parte dos frames, então não exige
    maioria absoluta — só que o quadrante vencedor tenha MAIS votos que
    qualquer outro (não uma contagem mínima fixa), o que já separa sinal de
    ruído sem descartar webcams reais mas difíceis de detectar.
    """
    if len(detections) < 2:
        return None

    quad_counts = Counter(_quadrant(b, frame_w, frame_h) for b in detections)
    ranked = quad_counts.most_common(2)
    best_quad, count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0
    if count < 2 or count <= second_count:
        return None

    matching = [b for b in detections if _quadrant(b, frame_w, frame_h) == best_quad]
    avg_x = sum(b[0] for b in matching) / len(matching)
    avg_y = sum(b[1] for b in matching) / len(matching)
    avg_w = sum(b[2] for b in matching) / len(matching)
    avg_h = sum(b[3] for b in matching) / len(matching)

    # O rosto é só uma fração da janela da webcam — expande a caixa pra
    # cobrir cabeça/ombros/moldura do overlay. Proporções calibradas
    # visualmente contra um overlay real de streamer: mais espaço embaixo
    # (ombros/corpo) do que em cima.
    margin_x = avg_w * 0.9
    margin_top = avg_h * 1.1
    margin_bottom = avg_h * 1.6

    x0 = max(0.0, avg_x - margin_x)
    y0 = max(0.0, avg_y - margin_top)
    x1 = min(float(frame_w), avg_x + avg_w + margin_x)
    y1 = min(float(frame_h), avg_y + avg_h + margin_bottom)

    region = (int(x0), int(y0), int(x1 - x0), int(y1 - y0))
    logger.info(
        "Janela do streamer detectada: %s (%d/%d amostras concordam).",
        region, count, sample_count,
    )
    return region


_PAN_SAMPLE_INTERVAL = 2.0  # segundos entre amostras de rastreamento horizontal
# Quanto a posição normalizada (0..1 da largura do frame) pode variar entre
# duas amostras consecutivas — evita "chicote" de câmera quando uma detecção
# isolada erra (ex: pega um objeto qualquer por 1 frame só). Combinado com a
# suavização abaixo, o resultado é um pan gradual em vez de saltos.
_PAN_MAX_STEP = 0.12
_PAN_SMOOTHING_ALPHA = 0.35  # média móvel exponencial


def _fill_gaps(values: list[float | None]) -> list[float]:
    """Interpola linearmente os `None` (amostras sem rosto detectado) usando
    os vizinhos válidos mais próximos; extremidades sem detecção ficam com o
    valor válido mais próximo (não dá pra interpolar além da borda)."""
    known = [i for i, v in enumerate(values) if v is not None]
    result = list(values)
    for a, b in zip(known, known[1:]):
        v0, v1 = values[a], values[b]
        for i in range(a + 1, b):
            frac = (i - a) / (b - a)
            result[i] = v0 + (v1 - v0) * frac
    for i in range(0, known[0]):
        result[i] = values[known[0]]
    for i in range(known[-1] + 1, len(values)):
        result[i] = values[known[-1]]
    return result  # type: ignore[return-value]


def _smooth(values: list[float]) -> list[float]:
    smoothed = [values[0]]
    for v in values[1:]:
        prev = smoothed[-1]
        delta = max(-_PAN_MAX_STEP, min(_PAN_MAX_STEP, v - prev))
        target = prev + delta
        smoothed.append(prev + _PAN_SMOOTHING_ALPHA * (target - prev))
    return smoothed


def track_horizontal_pan(
    video_path: Path,
    start: float,
    end: float,
    sample_interval: float = _PAN_SAMPLE_INTERVAL,
) -> list[tuple[float, float]] | None:
    """Rastreia a posição horizontal do rosto dentro do trecho [start, end]
    do vídeo (vídeo SOLO — webcam/vlog/gameplay com uma pessoa só, não pensado
    pra distinguir vários rostos), pra permitir um crop que "acompanha" a
    pessoa em vez do corte central fixo de sempre.

    Retorna uma lista de (tempo local desde `start`, posição x normalizada
    0..1 da largura do frame ORIGINAL), já com buracos preenchidos e
    suavizada — ou None se não achou rosto de forma confiável na maioria das
    amostras (best-effort, mesmo espírito de `detect_streamer_region`: melhor
    desistir e cair pro crop central do que seguir um rastro ruim).
    """
    duration = end - start
    if duration <= 0:
        return None

    cascade = _get_cascade()
    num_samples = max(2, int(duration / sample_interval) + 1)
    raw_t: list[float] = []
    raw_cx: list[float | None] = []
    frame_w = frame_h = 0

    with tempfile.TemporaryDirectory(prefix="pan_track_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for i in range(num_samples):
            local_t = min(i * sample_interval, duration)
            raw_t.append(local_t)
            frame_file = tmp_path / f"frame_{i}.jpg"
            if not _extract_frame(video_path, start + local_t, frame_file):
                raw_cx.append(None)
                continue
            image = cv2.imread(str(frame_file))
            if image is None:
                raw_cx.append(None)
                continue
            h, w = image.shape[:2]
            frame_w, frame_h = w, h
            gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

            best_face = None
            for min_neighbors in _MIN_NEIGHBORS_LEVELS:
                faces = cascade.detectMultiScale(
                    gray, scaleFactor=1.1, minNeighbors=min_neighbors,
                    minSize=(w // 15, h // 15),
                )
                if len(faces):
                    best_face = max(faces, key=lambda f: f[2] * f[3])  # maior rosto = sujeito principal
                    break
            if best_face is None:
                raw_cx.append(None)
                continue
            fx, fy, fw_, fh_ = best_face
            raw_cx.append((fx + fw_ / 2) / w)

    detected = sum(1 for v in raw_cx if v is not None)
    if frame_w == 0 or detected < max(2, len(raw_cx) // 2):
        logger.info(
            "Rastreamento de pan desistindo: rosto detectado em só %d/%d amostras — usando crop central.",
            detected, len(raw_cx),
        )
        return None

    filled = _fill_gaps(raw_cx)
    smoothed = _smooth(filled)
    logger.info("Rastreamento de pan OK: rosto detectado em %d/%d amostras.", detected, len(raw_cx))
    return list(zip(raw_t, smoothed))


def detect_streamer_region(
    video_path: Path,
    duration: float,
    sample_count: int = 14,
) -> tuple[int, int, int, int] | None:
    """Retorna (x, y, w, h) em pixels da resolução ORIGINAL do vídeo — já
    expandido além do rosto em si, pra cobrir a "moldura" da webcam — ou
    None se não achar uma região estável o bastante pra confiar.
    """
    if duration <= 0:
        return None

    cascade = _get_cascade()
    frames_gray: list = []
    frame_w = frame_h = 0

    with tempfile.TemporaryDirectory(prefix="streamer_detect_") as tmp_dir:
        tmp_path = Path(tmp_dir)
        for i in range(sample_count):
            # Espalha as amostras pelo vídeo inteiro, evitando os primeiros/
            # últimos instantes (tela de carregamento, intro etc.).
            timestamp = duration * (i + 1) / (sample_count + 1)
            frame_file = tmp_path / f"frame_{i}.jpg"
            if not _extract_frame(video_path, timestamp, frame_file):
                continue

            image = cv2.imread(str(frame_file))
            if image is None:
                continue
            h, w = image.shape[:2]
            frame_w, frame_h = w, h
            frames_gray.append(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY))

    if frame_w == 0 or frame_h == 0:
        logger.warning("Não foi possível extrair nenhum frame de %s pra detecção de rosto.", video_path)
        return None

    # Tenta do nível mais rigoroso pro mais tolerante — fica com o primeiro
    # que render uma região aceita (ver _MIN_NEIGHBORS_LEVELS acima pro motivo).
    for min_neighbors in _MIN_NEIGHBORS_LEVELS:
        detections: list[tuple[int, int, int, int]] = []
        for gray in frames_gray:
            faces = cascade.detectMultiScale(
                gray, scaleFactor=1.1, minNeighbors=min_neighbors,
                minSize=(frame_w // 20, frame_h // 20),
            )
            for (x, y, fw, fh) in faces:
                cx, cy = x + fw / 2, y + fh / 2
                # Só conta rosto perto de uma quina — o centro da tela
                # normalmente é o próprio jogo, não a webcam.
                near_corner = (cx < frame_w * 0.35 or cx > frame_w * 0.65) and (
                    cy < frame_h * 0.35 or cy > frame_h * 0.65
                )
                if near_corner:
                    detections.append((x, y, fw, fh))

        region = _pick_region(detections, frame_w, frame_h, sample_count)
        if region is not None:
            return region
        logger.info(
            "Sem janela estável com minNeighbors=%d (%d amostras com rosto na quina) — %s.",
            min_neighbors, len(detections),
            "tentando nível mais tolerante" if min_neighbors != _MIN_NEIGHBORS_LEVELS[-1] else "desistindo",
        )

    return None
