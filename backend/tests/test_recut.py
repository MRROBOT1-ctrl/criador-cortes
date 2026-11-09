"""POST /clips/{id}/recut: contrato §4 (remapeia em vez de apagar)."""
import uuid

import pytest

from app.services.video_cutter import FFmpegError

# Clipe [10, 30]: legenda em 20–22, zoom em 20 e 25, removido em 12–14 (absolutos).
EDITED = {
    "override": [{"start": 10.0, "end": 12.0, "text": "minha legenda"}],
    "pan_rel": [(10.0, 0.2), (15.0, 0.8)],
    "removed_rel": [(2.0, 4.0)],
}


@pytest.fixture
def edited(make_clip):
    return make_clip(**EDITED)


class TestRemapeamento:
    def test_edicoes_sobrevivem_com_o_mesmo_tempo_absoluto(self, client, edited):
        response = client.post(edited.recut_url, json={"start_time": 12.0, "end_time": 31.0})

        assert response.status_code == 200, response.text
        assert response.json()["revision"] == 1
        draft = client.get(edited.draft_url).json()
        assert draft["range"] == {"start": 12.0, "end": 31.0}
        assert draft["captions"] == {"mode": "manual", "lines": [{"start": 20.0, "end": 22.0, "text": "minha legenda"}]}
        assert draft["pan"] == [{"t": 20.0, "cx": 0.2}, {"t": 25.0, "cx": 0.8}]
        assert draft["removed"] == [{"start": 12.0, "end": 14.0}]

    def test_grava_valores_relativos_ao_novo_inicio(self, client, edited, read_clip):
        client.post(edited.recut_url, json={"start_time": 12.0, "end_time": 31.0})

        clip = read_clip(edited.clip_id)
        assert clip.get_caption_segments_override() == [{"start": 8.0, "end": 10.0, "text": "minha legenda"}]
        assert clip.get_manual_pan_keyframes() == [(8.0, 0.2), (13.0, 0.8)]
        assert clip.get_manual_removed_ranges() == [(0.0, 2.0)]
        assert clip.get_cut_segments() == [(2.0, 19.0)]

    def test_removido_parcial_e_cortado_na_borda(self, client, edited, read_clip):
        client.post(edited.recut_url, json={"start_time": 13.0})

        assert read_clip(edited.clip_id).get_manual_removed_ranges() == [(0.0, 1.0)]

    def test_o_que_fica_totalmente_fora_e_descartado_sem_409(self, client, edited, read_clip):
        response = client.post(edited.recut_url, json={"start_time": 15.0, "end_time": 24.0})

        assert response.status_code == 200, response.text
        clip = read_clip(edited.clip_id)
        assert clip.get_manual_removed_ranges() is None
        # O zoom em 25 saiu; sobra o de 20 e um ponto interpolado na borda 24.
        assert clip.get_manual_pan_keyframes() == [
            (5.0, 0.2),
            pytest.approx((9.0, 0.68)),
        ]

    def test_area_nova_da_legenda_manual_recebe_whisper_censurado(self, client, make_clip):
        sc = make_clip(profanity="censor", **EDITED)

        client.post(sc.recut_url, json={"end_time": 36.0})

        assert client.get(sc.draft_url).json()["captions"]["lines"] == [
            {"start": 20.0, "end": 22.0, "text": "minha legenda"},
            {"start": 32.0, "end": 36.0, "text": "essa ***** aqui fora"},
        ]

    def test_palavroes_sao_recalculados_para_a_janela_nova(self, client, make_clip, read_clip):
        sc = make_clip(profanity="censor")

        client.post(sc.recut_url, json={"start_time": 15.0, "end_time": 35.0})

        assert read_clip(sc.clip_id).get_mute_ranges() == [
            pytest.approx((0.55, 1.15)),
            pytest.approx((17.95, 18.55)),
        ]

    def test_legenda_automatica_sai_censurada(self, client, make_clip, fake_render):
        sc = make_clip(profanity="censor")

        client.post(sc.recut_url, json={"start_time": 12.0})

        assert "que ***** é essa" in fake_render.last.srt_text
        assert "porra" not in fake_render.last.srt_text

    def test_clipe_antigo_parte_da_derivacao_de_cut_segments(self, client, make_clip, read_clip):
        sc = make_clip(cut_rel=[(0.0, 5.0), (7.0, 20.0)])

        client.post(sc.recut_url, json={"start_time": 12.0})

        clip = read_clip(sc.clip_id)
        assert clip.get_manual_removed_ranges() == [(3.0, 5.0)]
        assert clip.get_cut_segments() == [(0.0, 3.0), (5.0, 18.0)]

    def test_sem_mudar_o_tempo_mantem_tudo(self, client, edited, read_clip, snapshot):
        before = snapshot(edited)["columns"]

        client.post(edited.recut_url, json={"caption_color": "#FF0000"})

        after = snapshot(edited)["columns"]
        for column in ("start_time", "end_time", "caption_segments_override", "manual_pan_keyframes", "manual_removed_ranges"):
            assert after[column] == before[column], column


class TestEstilo:
    def test_grava_so_os_campos_enviados(self, client, scenario, read_clip):
        response = client.post(scenario.recut_url, json={"caption_color": "#FF0000", "caption_font_size": 12})

        assert response.status_code == 200, response.text
        assert response.json()["caption_style"]["color"] == "#FF0000"
        clip = read_clip(scenario.clip_id)
        assert (clip.caption_style_color, clip.caption_style_font_size) == ("#FF0000", 12)
        assert clip.caption_style_preset is None
        assert clip.caption_style_enabled is None
        assert clip.caption_style_font_family is None

    def test_mapeia_os_cinco_campos_para_as_colunas(self, client, scenario, read_clip):
        client.post(scenario.recut_url, json={
            "captions_enabled": False, "caption_font_size": 10, "caption_color": "#00FF00",
            "caption_style": "boxed", "caption_font_family": "DejaVu Sans",
        })

        clip = read_clip(scenario.clip_id)
        assert (
            clip.caption_style_enabled, clip.caption_style_font_size, clip.caption_style_color,
            clip.caption_style_preset, clip.caption_style_font_family,
        ) == (False, 10, "#00FF00", "boxed", "DejaVu Sans")

    def test_campo_omitido_cai_no_estilo_efetivo_do_clipe(self, client, make_clip, fake_render):
        sc = make_clip(caption_style_color="#123456")

        client.post(sc.recut_url, json={"caption_font_size": 12})

        assert fake_render.last.kwargs["caption_color"] == "#123456"
        assert fake_render.last.kwargs["caption_font_size"] == 12

    def test_estilo_do_recut_nao_se_perde_num_put_pan_seguinte(self, client, scenario, fake_render):
        client.post(scenario.recut_url, json={"caption_color": "#FF0000"})

        response = client.put(f"/clips/{scenario.clip_id}/pan", json={"keyframes": [{"t": 1.0, "cx": 0.5}]})

        assert response.status_code == 200, response.text
        assert fake_render.last.kwargs["caption_color"] == "#FF0000"


class TestErrosNoFormatoAntigo:
    @pytest.mark.parametrize(
        "payload",
        [
            {"start_time": 20.0, "end_time": 10.0},
            {"start_time": 100.0, "end_time": 121.0},
            {"caption_color": "vermelho"},
            {"caption_font_size": 99},
        ],
    )
    def test_400_detail_e_texto(self, client, scenario, payload):
        response = client.post(scenario.recut_url, json=payload)

        assert response.status_code == 400, response.text
        assert isinstance(response.json()["detail"], str)

    def test_404_clipe_inexistente(self, client):
        response = client.post(f"/clips/{uuid.uuid4().hex}/recut", json={})

        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)

    def test_404_video_original_ausente(self, client, make_clip):
        response = client.post(make_clip(video_exists=False).recut_url, json={})

        assert response.status_code == 404
        assert isinstance(response.json()["detail"], str)

    def test_409_sem_arquivo_processado(self, client, make_clip):
        response = client.post(make_clip(rendered=False).recut_url, json={})

        assert response.status_code == 409
        assert isinstance(response.json()["detail"], str)

    def test_nothing_left_no_formato_antigo(self, client, make_clip):
        response = client.post(make_clip(removed_rel=[(2.0, 20.0)]).recut_url, json={"start_time": 12.0})

        assert response.status_code == 400
        assert isinstance(response.json()["detail"], str)

    def test_falha_no_ffmpeg_e_500_e_nao_muda_nada(self, client, edited, fake_render, snapshot):
        before = snapshot(edited)
        fake_render.error = FFmpegError("Conversion failed!")

        response = client.post(edited.recut_url, json={"start_time": 12.0})

        assert response.status_code == 500
        assert snapshot(edited) == before
