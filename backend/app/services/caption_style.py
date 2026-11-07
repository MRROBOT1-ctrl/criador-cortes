"""Resolução do estilo EFETIVO da legenda de um clipe.

Regra (docs/spec-timeline-v1.md §2.1): cada campo é resolvido
INDEPENDENTEMENTE, na ordem Clip -> Job -> default fixo. NULL no Clip quer
dizer "herda do Job"; NULL no Job (linha antiga, criada antes da coluna ter
default) cai no default.

Função pura — sem banco e sem I/O — pra ser usada por TODOS os caminhos de
renderização (render_clip/safe_rerender, /edit, recut, pan, captions, cuts) e
pelo _clip_to_out. Assim o estilo que aparece na resposta da API é
exatamente o que vai pro vídeo, e o pipeline.py não precisa mudar (clipes
gerados por ele têm as colunas caption_style_* em NULL e herdam do Job).

Usa getattr em vez de importar Clip/Job: evita import circular com
app.models e deixa a função testável com qualquer objeto (ou None).
"""
from typing import Any

from app.schemas import ClipCaptionStyle

# Defaults fixos — os mesmos de Job.captions_enabled/caption_font_size/
# caption_color/caption_style/caption_font_family em app/models.py.
DEFAULT_CAPTION_STYLE = ClipCaptionStyle(
    enabled=True,
    font_size=8,
    color="#FFFFFF",
    preset="bold_outline",
    font_family="DejaVu Sans",
)

# campo do ClipCaptionStyle -> (coluna no Clip, coluna no Job). Os nomes do
# Job são os antigos (caption_style = preset), por isso o mapa explícito.
_FIELD_COLUMNS: dict[str, tuple[str, str]] = {
    "enabled": ("caption_style_enabled", "captions_enabled"),
    "font_size": ("caption_style_font_size", "caption_font_size"),
    "color": ("caption_style_color", "caption_color"),
    "preset": ("caption_style_preset", "caption_style"),
    "font_family": ("caption_style_font_family", "caption_font_family"),
}


def resolve_caption_style(clip: Any | None, job: Any | None = None) -> ClipCaptionStyle:
    """Estilo efetivo, campo a campo: Clip > Job > DEFAULT_CAPTION_STYLE.

    - `clip`: um app.models.Clip (ou None).
    - `job`: o Job do clipe. Se None, tenta `clip.job` (a relationship) —
      quem já tem o Job em mãos pode passar direto e evitar o lazy load.

    Só `None` conta como "não definido": `enabled=False` no Clip vence um
    `True` do Job (por isso `is not None`, e não `or`). Não valida os valores
    (isso é do router que grava); só resolve.
    """
    if job is None and clip is not None:
        job = getattr(clip, "job", None)

    values: dict[str, Any] = {}
    for field, (clip_col, job_col) in _FIELD_COLUMNS.items():
        value = getattr(clip, clip_col, None) if clip is not None else None
        if value is None and job is not None:
            value = getattr(job, job_col, None)
        if value is None:
            value = getattr(DEFAULT_CAPTION_STYLE, field)
        values[field] = value
    return ClipCaptionStyle(**values)
