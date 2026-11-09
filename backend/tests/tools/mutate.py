"""
Teste de mutação manual da suíte do backend.

Injeta um bug por vez numa CÓPIA descartável de app/ e tests/ e confere se a
suíte falha. O código real, o banco e os vídeos nunca são tocados.

Uso, a partir de backend/ e com a .venv ativa:

    python tests/tools/mutate.py

Leva alguns minutos: bugs de lock fazem testes esperarem os timeouts. A lista
abaixo foi escolhida à mão, nos pontos críticos do contrato; não substitui uma
ferramenta de mutação exaustiva. Termina com código 1 se algum bug sobreviver
ou se algum trecho procurado não existir mais no código.
"""
import shutil
import subprocess
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
WORKERS = 6
RUN_TIMEOUT_SECONDS = 420

CE, CR, WD = "app/services/clip_edit.py", "app/services/clip_rendering.py", "app/routers/words.py"

# (nome, arquivo, trecho original, trecho com o bug)
MUTATIONS = [
    ("auto sem censura", CE, "    if censor:\n        overlapping = profanity.redact_segments(overlapping)", "    if False:\n        overlapping = profanity.redact_segments(overlapping)"),
    ("area nova sem censura", CE, "(profanity.redact_text(seg.text) if censor else seg.text).strip()", "seg.text.strip()"),
    ("sem checar revision", CE, "if payload.revision != (clip.revision or 0):", "if False:"),
    ("CAS sem revision", CE, ".where(Clip.id == clip.id, Clip.revision == base_revision)", ".where(Clip.id == clip.id)"),
    ("lock nao bloqueia", CR, "if clip_id in _busy_clips:", "if False:"),
    ("lock global", CR, "if clip_id in _busy_clips:", "if _busy_clips:"),
    ("lock nao libera", CR, "_busy_clips.discard(clip_id)", "pass"),
    ("perdas sem confirmar", CE, "if plan.losses and not payload.confirm_losses:", "if False:"),
    ("to_absolute errado", CE, "return t_rel + clip_start", "return t_rel"),
    ("to_relative errado", CE, "return t_abs - clip_start", "return t_abs"),
    ("sobrescreve arquivo", CR, 'return Path(clip.processed_path).parent / f"clip_{clip.index}_r{(clip.revision or 0) + 1}.mp4"', "return Path(clip.processed_path)"),
    ("nao apaga antigo", CR, "    if not old_path or Path(old_path) == new_path:\n        return", "    return"),
    ("dry_run renderiza", CE, "    if dry_run:\n", "    if False:\n"),
    ("words sem truncated", WD, "truncated = True", "truncated = False"),
    ("words sem janela 600", WD, "end = start + MAX_WINDOW_SECONDS\n        truncated", "truncated"),
    ("words vaza palavrao", WD, "profanity.redact_text(raw) if censored else raw", "raw"),
    ("words sem cache", WD, 'headers={"Cache-Control": CACHE_CONTROL}', "headers={}"),
    ("words nao-pronto vira 404", WD, 'if job.status in ("pending", "running"):', "if False:"),
    ("recut zera removidos", CE, "removed=state.removed,\n        segments=segments", "removed=[],\n        segments=segments"),
    ("recut nao grava estilo", CE, "style_columns = {STYLE_COLUMNS[name]: value for name, value in style_overrides.items()}", "style_columns = {}"),
    ("edit nao grava estilo", CE, "style_columns = {STYLE_COLUMNS[name]: getattr(style, name) for name in STYLE_COLUMNS}", "style_columns = {}"),
    ("sem interpolar zoom", CE, "ratio = (t - p0.t) / (p1.t - p0.t)", "ratio = 0.0"),
    ("sem mute", CE, "plan.mute_ranges_rel = ranges_to_relative(prof, new_start) or None", "plan.mute_ranges_rel = None"),
    ("encostar nao e perda", CE, 'if _overlap(a, b, new_start, new_end) <= _EPS:\n            losses.append(EditChange(kind="caption"', 'if _overlap(a, b, new_start, new_end) < -_EPS:\n            losses.append(EditChange(kind="caption"'),
    ("commit nao apaga novo", CE, "    except Exception:\n        db.rollback()\n        new_path.unlink(missing_ok=True)\n        raise", "    except Exception:\n        db.rollback()\n        raise"),
    ("CAS nao apaga novo", CE, "            db.rollback()\n            new_path.unlink(missing_ok=True)\n            return False", "            db.rollback()\n            return False"),
    ("antigos sem revision", CR, "clip.revision = (clip.revision or 0) + 1", "pass"),
    ("draft censura manual", CE, 'text=s["text"])\n            for s in caption_override_rel', 'text=profanity.redact_text(s["text"]))\n            for s in caption_override_rel'),
    ("sem nothing_left", CE, "if kept_duration(keep_rel, new_start, new_end) < MIN_CLIP_DURATION:", "if False:"),
    ("derivacao nao tira filtro", CE, "leftovers = subtract_ranges(holes_abs, profanity_cut_abs)", "leftovers = holes_abs"),
    ("sem arredondar range", CE, "range=TimeRange(start=r3(start), end=r3(end)),", "range=TimeRange(start=start, end=end),"),
    ("download arquivo fixo", "app/routers/download.py", "    path = Path(clip.processed_path)\n    return _ranged", "    path = Path(clip.processed_path).parent / 'clip_0.mp4'\n    return _ranged"),
]


def run_mutation(mutation: tuple[str, str, str, str]) -> tuple[str, str, str]:
    name, relative_path, original, mutated = mutation
    with tempfile.TemporaryDirectory(prefix="mutacao-") as tmp:
        root = Path(tmp) / "backend"
        root.mkdir()
        for directory in ("app", "tests", "static"):
            shutil.copytree(BACKEND_DIR / directory, root / directory, ignore=shutil.ignore_patterns("__pycache__"))

        target = root / relative_path
        source = target.read_text(encoding="utf-8")
        if original not in source:
            return name, "NAO-APLICOU", "trecho não encontrado"
        target.write_text(source.replace(original, mutated, 1), encoding="utf-8")

        result = subprocess.run(
            [sys.executable, "-I", "-m", "pytest", "tests", "-q", "--tb=no", "-p", "no:cacheprovider", "-W", "ignore"],
            cwd=root, capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS,
        )
        output = result.stdout.strip().splitlines()
        summary = output[-1] if output else result.stderr[-200:]
        status = {0: "SOBREVIVEU", 1: "PEGOU"}.get(result.returncode, f"ERRO-{result.returncode}")
        return name, status, summary


def main() -> int:
    with ThreadPoolExecutor(WORKERS) as pool:
        results = list(pool.map(run_mutation, MUTATIONS))

    for name, status, summary in results:
        print(f"{status:12} {name:28} {summary}")

    caught = sum(status == "PEGOU" for _, status, _ in results)
    print(f"\n{caught}/{len(results)} bugs detectados")
    return 0 if caught == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
