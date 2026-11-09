"""Arquivos clip_{index}_r{n}.mp4 e download: contrato §0.5."""
import io
import uuid
import zipfile
from pathlib import Path

import pytest


def mp4_names(sc):
    return sorted(p.name for p in sc.output_dir.iterdir() if p.suffix == ".mp4")


LEGACY_ENDPOINTS = [
    pytest.param("pan", {"keyframes": [{"t": 1.0, "cx": 0.2}, {"t": 9.0, "cx": 0.8}]}, id="pan"),
    pytest.param("captions", {"segments": [{"start": 1.0, "end": 3.0, "text": "oi"}]}, id="captions"),
    pytest.param("cuts", {"keep_segments": [[0.0, 5.0], [8.0, 20.0]]}, id="cuts"),
]


def test_nome_muda_a_cada_revisao(client, scenario, edit_body, read_clip):
    assert mp4_names(scenario) == ["clip_0.mp4"]

    for revision in range(3):
        response = client.put(scenario.edit_url, json=edit_body(revision=revision))

        assert response.status_code == 200, response.text
        expected = f"clip_0_r{revision + 1}.mp4"
        assert Path(response.json()["clip"]["processed_path"]).name == expected
        assert mp4_names(scenario) == [expected]
        clip = read_clip(scenario.clip_id)
        assert Path(clip.processed_path) == scenario.output_dir / expected
        assert clip.revision == revision + 1


def test_arquivo_atual_nunca_e_sobrescrito_durante_o_render(client, scenario, edit_body, fake_render):
    seen = {}

    def inspect(call):
        seen["output"] = call.output_path.name
        seen["atual"] = scenario.clip_path.read_bytes()

    fake_render.hook = inspect

    client.put(scenario.edit_url, json=edit_body())

    assert "clip_0_r1" in seen["output"]
    assert seen["output"] != scenario.clip_path.name
    assert seen["atual"] == b"render-original"


def test_nao_sobra_arquivo_temporario(client, scenario, edit_body, fake_render):
    client.put(scenario.edit_url, json=edit_body())

    assert sorted(p.name for p in scenario.output_dir.iterdir()) == ["clip_0_r1.mp4"]
    assert not list(scenario.data_dir.glob("*.srt"))


def test_download_usa_processed_path(client, scenario, edit_body):
    assert client.get(scenario.file_url).content == b"render-original"

    client.put(scenario.edit_url, json=edit_body())
    first = client.get(scenario.file_url)
    assert first.status_code == 200
    assert first.content == b"render-1"
    assert "clip_0_r1.mp4" in first.headers["content-disposition"]

    client.put(scenario.edit_url, json=edit_body(revision=1))
    assert client.get(scenario.file_url).content == b"render-2"


def test_etag_muda_depois_de_um_render(client, scenario, edit_body):
    before = client.get(scenario.file_url).headers["etag"]

    client.put(scenario.edit_url, json=edit_body())

    assert client.get(scenario.file_url).headers["etag"] != before


def test_download_com_range(client, scenario, edit_body):
    client.put(scenario.edit_url, json=edit_body())

    response = client.get(scenario.file_url, headers={"Range": "bytes=0-5"})

    assert response.status_code == 206
    assert response.content == b"render"


def test_download_sem_arquivo_responde_404(client, make_clip):
    assert client.get(make_clip(rendered=False).file_url).status_code == 404
    assert client.get(f"/clips/{uuid.uuid4().hex}/file").status_code == 404


def test_zip_do_job_leva_a_versao_atual(client, scenario, edit_body):
    client.put(scenario.edit_url, json=edit_body())

    response = client.get(f"/jobs/{scenario.job_id}/download")

    assert response.status_code == 200, response.text
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        assert archive.namelist() == ["clip_0_r1.mp4"]
        assert archive.read("clip_0_r1.mp4") == b"render-1"


def test_lista_de_clipes_traz_revision_e_caminho_novo(client, scenario, edit_body):
    client.put(scenario.edit_url, json=edit_body())

    item = client.get(f"/jobs/{scenario.job_id}/clips").json()[0]

    assert item["revision"] == 1
    assert item["processed_path"].endswith("clip_0_r1.mp4")


def test_recut_tambem_gera_arquivo_novo(client, scenario):
    response = client.post(scenario.recut_url, json={"start_time": 12.0})

    assert response.status_code == 200, response.text
    assert response.json()["processed_path"].endswith("clip_0_r1.mp4")
    assert mp4_names(scenario) == ["clip_0_r1.mp4"]


@pytest.mark.parametrize(("endpoint", "payload"), LEGACY_ENDPOINTS)
def test_endpoints_antigos_incrementam_revision_e_geram_arquivo_novo(client, scenario, read_clip, endpoint, payload):
    response = client.put(f"/clips/{scenario.clip_id}/{endpoint}", json=payload)

    assert response.status_code == 200, response.text
    assert response.json()["revision"] == 1
    assert mp4_names(scenario) == ["clip_0_r1.mp4"]
    assert read_clip(scenario.clip_id).revision == 1


@pytest.mark.parametrize(("endpoint", "payload"), LEGACY_ENDPOINTS)
def test_gravacao_antiga_invalida_rascunho_do_edit(client, scenario, edit_body, endpoint, payload):
    assert client.put(f"/clips/{scenario.clip_id}/{endpoint}", json=payload).status_code == 200

    response = client.put(scenario.edit_url, json=edit_body(revision=0))

    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "revision_conflict"
    assert response.json()["detail"]["current_revision"] == 1


def test_put_cuts_zera_manual_removed_ranges(client, make_clip, read_clip):
    sc = make_clip(removed_rel=[(2.0, 4.0)])

    client.put(f"/clips/{sc.clip_id}/cuts", json={"keep_segments": [[0.0, 5.0], [8.0, 20.0]]})

    assert read_clip(sc.clip_id).get_manual_removed_ranges() is None
    assert client.get(sc.draft_url).json()["removed"] == [{"start": 15.0, "end": 18.0}]
