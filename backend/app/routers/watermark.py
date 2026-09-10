"""
Marca d'água/logo — uma imagem só, global pro projeto inteiro (não por
usuário/job: o MVP não tem multi-user, então "sua logo" é uma coisa só).
Fica salva em `settings.data_dir/watermark.<ext>`; cada Job decide se aplica
ou não (Job.watermark_enabled) e em que posição (Job.watermark_position).
"""
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse

from app.config import settings
from app.schemas import WatermarkStatus

router = APIRouter(tags=["watermark"])

ALLOWED_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def find_watermark_file() -> Path | None:
    matches = sorted(settings.data_dir.glob("watermark.*"))
    return matches[0] if matches else None


@router.get("/watermark", response_model=WatermarkStatus)
def get_watermark_status():
    return WatermarkStatus(exists=find_watermark_file() is not None)


@router.get("/watermark/file")
def get_watermark_file():
    path = find_watermark_file()
    if path is None:
        raise HTTPException(status_code=404, detail="Nenhuma marca d'água configurada ainda.")
    return FileResponse(path)


@router.post("/watermark", response_model=WatermarkStatus)
async def upload_watermark(file: UploadFile = File(...)):
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=400,
            detail=f"Formato '{ext}' não suportado. Use: {', '.join(sorted(ALLOWED_EXTENSIONS))}.",
        )

    # Remove qualquer logo anterior (extensão pode ter mudado) antes de salvar
    # a nova, senão find_watermark_file() poderia pegar a antiga por ordem
    # alfabética caso o usuário troque de .png pra .jpg, por exemplo.
    for old in settings.data_dir.glob("watermark.*"):
        old.unlink()

    dest_path = settings.data_dir / f"watermark{ext}"
    content = await file.read()
    dest_path.write_bytes(content)

    return WatermarkStatus(exists=True)


@router.delete("/watermark", response_model=WatermarkStatus)
def delete_watermark():
    for old in settings.data_dir.glob("watermark.*"):
        old.unlink()
    return WatermarkStatus(exists=False)
