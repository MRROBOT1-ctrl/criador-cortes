"""GET /clips/{id}/draft: contrato §2."""
import uuid

import pytest

DRAFT_KEYS = {
    "clip_id", "job_id", "revision", "video_duration", "has_streamer_region",
    "range", "caption_style", "captions", "pan", "removed", "profanity",
}
STYLE_KEYS = {"enabled", "font_size", "color", "preset", "font_family"}


def test_formato_do_contrato(client, scenario):
    response = client.get(scenario.draft_url)

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == DRAFT_KEYS
    assert set(body["caption_style"]) == STYLE_KEYS
    assert set(body["captions"]) == {"mode", "lines"}
    assert set(body["profanity"]) == {"mode", "ranges"}
    assert body["clip_id"] == scenario.clip_id
    assert body["job_id"] == scenario.job_id
    assert body["revision"] == 0
    assert body["video_duration"] == 120.0
    assert body["has_streamer_region"] is False
    assert body["range"] == {"start": 10.0, "end": 30.0}
    assert body["pan"] == []
    assert body["removed"] == []


def test_modo_auto_traz_falas_do_whisper_no_corte(client, scenario):
    captions = client.get(scenario.draft_url).json()["captions"]

    assert captions["mode"] == "auto"
    assert [ln["text"] for ln in captions["lines"]] == [
        "olá pessoal tudo bem", "que porra é essa", "isso é incrível", "vamos continuar agora",
    ]


@pytest.mark.parametrize("mode", ["censor", "cut"])
def test_modo_auto_censurado_com_filtro_ligado(client, make_clip, mode):
    response = client.get(make_clip(profanity=mode).draft_url)

    assert "porra" not in response.text
    assert "que ***** é essa" in [ln["text"] for ln in response.json()["captions"]["lines"]]


def test_modo_manual_converte_para_absoluto_sem_censura(client, make_clip):
    sc = make_clip(profanity="censor", override=[{"start": 1.5, "end": 3.0, "text": "que porra linda"}])

    captions = client.get(sc.draft_url).json()["captions"]

    assert captions == {"mode": "manual", "lines": [{"start": 11.5, "end": 13.0, "text": "que porra linda"}]}


def test_pan_e_removed_em_absoluto(client, make_clip):
    sc = make_clip(pan_rel=[(1.0, 0.2), (9.0, 0.8)], removed_rel=[(5.0, 7.0)])

    body = client.get(sc.draft_url).json()

    assert body["pan"] == [{"t": 11.0, "cx": 0.2}, {"t": 19.0, "cx": 0.8}]
    assert body["removed"] == [{"start": 15.0, "end": 17.0}]


def test_listas_vem_em_ordem_crescente(client, make_clip):
    sc = make_clip(
        override=[{"start": 8.0, "end": 9.0, "text": "b"}, {"start": 1.0, "end": 2.0, "text": "a"}],
        pan_rel=[(9.0, 0.8), (1.0, 0.2)],
        removed_rel=[(12.0, 13.0), (5.0, 7.0)],
    )

    body = client.get(sc.draft_url).json()

    assert [ln["text"] for ln in body["captions"]["lines"]] == ["a", "b"]
    assert [p["t"] for p in body["pan"]] == [11.0, 19.0]
    assert [r["start"] for r in body["removed"]] == [15.0, 22.0]


def test_profanity_censor_e_cut(client, make_clip):
    muted = client.get(make_clip(profanity="censor").draft_url).json()["profanity"]
    cut = client.get(make_clip(profanity="cut").draft_url).json()["profanity"]

    assert muted == {"mode": "censor", "ranges": [{"start": 15.55, "end": 16.15, "kind": "muted"}]}
    assert cut == {"mode": "cut", "ranges": [{"start": 15.55, "end": 16.15, "kind": "cut"}]}


def test_estilo_efetivo_clip_sobre_job(client, make_clip):
    sc = make_clip(caption_style_color="#123456", caption_style_font_size=14)

    style = client.get(sc.draft_url).json()["caption_style"]

    assert style["color"] == "#123456"
    assert style["font_size"] == 14
    # Os campos sem coluna própria no clipe vêm do Job/default e nunca nulos.
    assert style["preset"] == "bold_outline"
    assert all(value is not None for value in style.values())


def test_tela_dividida(client, make_clip):
    assert client.get(make_clip(streamer_region="0,0,100,100").draft_url).json()["has_streamer_region"] is True


def test_tempos_arredondados_em_tres_casas(client, make_clip):
    sc = make_clip(start=10.12345, end=29.98765)

    assert client.get(sc.draft_url).json()["range"] == {"start": 10.123, "end": 29.988}


def test_so_leitura(client, scenario, snapshot, fake_render):
    before = snapshot(scenario)

    client.get(scenario.draft_url)

    assert snapshot(scenario) == before
    assert fake_render.calls == []


def test_clipe_inexistente(client):
    response = client.get(f"/clips/{uuid.uuid4().hex}/draft")

    assert response.status_code == 404
    assert response.json()["detail"] == {"code": "not_found", "message": "Corte não encontrado."}


class TestClipeAntigo:
    def test_deriva_removed_dos_buracos_de_cut_segments(self, client, make_clip):
        sc = make_clip(cut_rel=[(0.0, 5.0), (7.0, 20.0)])

        assert client.get(sc.draft_url).json()["removed"] == [{"start": 15.0, "end": 17.0}]

    def test_no_modo_cut_tira_o_que_e_do_filtro(self, client, make_clip):
        sc = make_clip(profanity="cut", cut_rel=[(0.0, 5.55), (6.15, 12.0), (13.0, 20.0)])

        assert client.get(sc.draft_url).json()["removed"] == [{"start": 22.0, "end": 23.0}]

    def test_descarta_sobra_menor_que_cinco_centesimos(self, client, make_clip):
        sc = make_clip(cut_rel=[(0.0, 5.0), (5.02, 20.0)])

        assert client.get(sc.draft_url).json()["removed"] == []

    def test_sem_cut_segments_nao_ha_removed(self, client, scenario):
        assert client.get(scenario.draft_url).json()["removed"] == []
