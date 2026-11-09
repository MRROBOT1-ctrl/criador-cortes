"""PUT /clips/{id}/edit: contrato §1 e regras de remapeamento da spec §4."""
import json
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy.orm import Session

from app.services.video_cutter import FFmpegError


def detail(response):
    return response.json()["detail"]


def lines_of(body):
    return [(ln["start"], ln["end"], ln["text"]) for ln in body["draft"]["captions"]["lines"]]


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert detail(response)["code"] == code
    assert isinstance(detail(response)["message"], str) and detail(response)["message"]


# --------------------------------------------------------------------------
# dry_run e perdas
# --------------------------------------------------------------------------


class TestDryRun:
    def test_sem_perdas_responde_200_e_nao_grava(self, client, scenario, edit_body, fake_render, snapshot):
        before = snapshot(scenario)

        response = client.put(f"{scenario.edit_url}?dry_run=true", json=edit_body(start=12.0, end=28.0))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "dry_run"
        assert body["render_seconds"] is None
        assert body["adjustments"] == []
        assert body["clip"]["revision"] == 0
        assert body["clip"]["start_time"] == 10.0
        assert body["draft"]["revision"] == 0
        assert body["draft"]["range"] == {"start": 12.0, "end": 28.0}
        assert fake_render.calls == []
        assert snapshot(scenario) == before

    def test_com_perdas_responde_409_com_a_lista(self, client, scenario, edit_body, fake_render, snapshot):
        before = snapshot(scenario)
        payload = edit_body(
            start=15.0, end=25.0,
            lines=[(11.0, 13.0, "fora"), (20.0, 22.0, "dentro")],
            pan=[(12.0, 0.2), (20.0, 0.5)],
            removed=[(26.0, 27.0)],
        )

        response = client.put(f"{scenario.edit_url}?dry_run=true", json=payload)

        assert_error(response, 409, "losses_require_confirmation")
        assert detail(response)["losses"] == [
            {"kind": "caption", "start": 11.0, "end": 13.0, "text": "fora"},
            {"kind": "pan", "start": 12.0, "end": 12.0},
            {"kind": "removed", "start": 26.0, "end": 27.0},
        ]
        assert "3 itens" in detail(response)["message"]
        assert fake_render.calls == []
        assert snapshot(scenario) == before

    def test_confirm_losses_no_dry_run_responde_200_sem_gravar(self, client, scenario, edit_body, snapshot):
        before = snapshot(scenario)
        payload = edit_body(start=15.0, end=25.0, lines=[(11.0, 13.0, "fora"), (20.0, 22.0, "fica")], confirm_losses=True)

        response = client.put(f"{scenario.edit_url}?dry_run=true", json=payload)

        assert response.status_code == 200, response.text
        assert lines_of(response.json()) == [(20.0, 22.0, "fica")]
        assert snapshot(scenario) == before

    def test_dry_run_tambem_detecta_revision_conflict(self, client, scenario, edit_body):
        response = client.put(f"{scenario.edit_url}?dry_run=true", json=edit_body(revision=7))
        assert_error(response, 409, "revision_conflict")
        assert detail(response)["current_revision"] == 0

    def test_dry_run_nao_disputa_o_lock(self, client, scenario, edit_body, render_gate):
        with ThreadPoolExecutor(1) as pool:
            running = pool.submit(client.put, scenario.edit_url, json=edit_body())
            assert render_gate.entered.wait(10)

            response = client.put(f"{scenario.edit_url}?dry_run=true", json=edit_body())

            render_gate.release.set()
            assert running.result(30).status_code == 200
        assert response.status_code == 200, response.text


class TestConfirmLosses:
    def test_sem_confirmar_nao_renderiza(self, client, scenario, edit_body, fake_render, snapshot):
        before = snapshot(scenario)
        response = client.put(scenario.edit_url, json=edit_body(start=15.0, lines=[(11.0, 13.0, "fora"), (20.0, 22.0, "fica")]))
        assert_error(response, 409, "losses_require_confirmation")
        assert fake_render.calls == []
        assert snapshot(scenario) == before

    def test_confirmando_renderiza_e_descarta_as_perdas(self, client, scenario, edit_body, read_clip):
        payload = edit_body(
            start=15.0, end=25.0,
            lines=[(11.0, 13.0, "fora"), (20.0, 22.0, "dentro")],
            pan=[(12.0, 0.2), (20.0, 0.5)],
            removed=[(26.0, 27.0)],
            confirm_losses=True,
        )

        response = client.put(scenario.edit_url, json=payload)

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["status"] == "rendered"
        assert isinstance(body["render_seconds"], (int, float))
        assert body["clip"]["revision"] == 1
        assert body["draft"]["revision"] == 1
        assert lines_of(body) == [(20.0, 22.0, "dentro")]
        assert body["draft"]["removed"] == []
        clip = read_clip(scenario.clip_id)
        assert (clip.start_time, clip.end_time, clip.revision) == (15.0, 25.0, 1)
        assert clip.get_manual_removed_ranges() is None

    def test_perdas_e_ajustes_so_tem_os_campos_que_se_aplicam(self, client, scenario, edit_body):
        payload = edit_body(start=15.0, lines=[(12.0, 18.0, "parcial")], pan=[(12.0, 0.2), (20.0, 0.5)], confirm_losses=True)

        body = client.put(scenario.edit_url, json=payload).json()

        by_kind = {item["kind"]: item for item in body["adjustments"]}
        assert "text" not in by_kind["pan"]
        assert by_kind["caption"]["text"] == "parcial"
        assert all("change" in item for item in body["adjustments"])

        losses = detail(client.put(scenario.edit_url, json=edit_body(revision=1, start=25.0, pan=[(20.0, 0.5)])))["losses"]
        assert losses == [{"kind": "pan", "start": 20.0, "end": 20.0}]


# --------------------------------------------------------------------------
# Conflitos e falhas
# --------------------------------------------------------------------------


class TestConflitosEFalhas:
    def test_revision_conflict_com_corpo_antigo_reenviado(self, client, scenario, edit_body, fake_render, snapshot):
        payload = edit_body(start=12.0)
        assert client.put(scenario.edit_url, json=payload).status_code == 200
        after_first = snapshot(scenario)

        response = client.put(scenario.edit_url, json=payload)

        assert_error(response, 409, "revision_conflict")
        assert detail(response)["current_revision"] == 1
        assert len(fake_render.calls) == 1
        assert snapshot(scenario) == after_first

    def test_render_in_progress(self, client, scenario, edit_body, render_gate, snapshot, fake_render):
        before = snapshot(scenario)
        with ThreadPoolExecutor(1) as pool:
            running = pool.submit(client.put, scenario.edit_url, json=edit_body())
            assert render_gate.entered.wait(10)

            blocked = client.put(scenario.edit_url, json=edit_body())

            assert_error(blocked, 409, "render_in_progress")
            assert snapshot(scenario) == before
            render_gate.release.set()
            assert running.result(30).status_code == 200
        assert len(fake_render.calls) == 1

    def test_render_failed_deixa_banco_e_arquivo_intactos(self, client, scenario, edit_body, fake_render, snapshot):
        before = snapshot(scenario)
        fake_render.error = FFmpegError("frame=1\nframe=2\nConversion failed!")

        response = client.put(scenario.edit_url, json=edit_body(start=12.0))

        assert_error(response, 500, "render_failed")
        assert "Conversion failed!" in detail(response)["ffmpeg_tail"]
        assert snapshot(scenario) == before

    def test_erro_generico_no_render_nao_traz_ffmpeg_tail(self, client, scenario, edit_body, fake_render, snapshot):
        before = snapshot(scenario)
        fake_render.error = OSError("disco cheio")

        response = client.put(scenario.edit_url, json=edit_body())

        assert_error(response, 500, "render_failed")
        assert "ffmpeg_tail" not in detail(response)
        assert snapshot(scenario) == before

    def test_lock_e_liberado_depois_da_falha(self, client, scenario, edit_body, fake_render):
        fake_render.error = FFmpegError("Conversion failed!")
        assert client.put(scenario.edit_url, json=edit_body()).status_code == 500

        fake_render.error = None

        assert client.put(scenario.edit_url, json=edit_body()).status_code == 200

    def test_falha_no_commit_apaga_o_arquivo_novo(self, client, scenario, edit_body, snapshot, monkeypatch):
        before = snapshot(scenario)

        def broken_commit(_self):
            raise RuntimeError("commit falhou")

        with monkeypatch.context() as patch:
            patch.setattr(Session, "commit", broken_commit)
            response = client.put(scenario.edit_url, json=edit_body(start=12.0))

        assert_error(response, 500, "render_failed")
        assert snapshot(scenario) == before


# --------------------------------------------------------------------------
# Remapeamento
# --------------------------------------------------------------------------


class TestRemapeamento:
    def test_shift_positivo_mantem_absoluto_e_preenche_area_nova(self, client, scenario, edit_body, read_clip):
        response = client.put(scenario.edit_url, json=edit_body(start=15.0, end=35.0, lines=[(20.0, 22.0, "minha fala")]))

        assert response.status_code == 200, response.text
        body = response.json()
        assert lines_of(body) == [(20.0, 22.0, "minha fala"), (32.0, 35.0, "essa merda aqui fora")]
        assert "área nova" in body["adjustments"][0]["change"]
        assert read_clip(scenario.clip_id).get_caption_segments_override() == [
            {"start": 5.0, "end": 7.0, "text": "minha fala"},
            {"start": 17.0, "end": 20.0, "text": "essa merda aqui fora"},
        ]

    def test_shift_negativo_mantem_absoluto_e_preenche_area_nova(self, client, scenario, edit_body, read_clip):
        response = client.put(scenario.edit_url, json=edit_body(start=5.0, end=25.0, lines=[(20.0, 22.0, "minha fala")]))

        assert response.status_code == 200, response.text
        assert lines_of(response.json()) == [(7.0, 9.5, "logo antes do corte"), (20.0, 22.0, "minha fala")]
        assert read_clip(scenario.clip_id).get_caption_segments_override() == [
            {"start": 2.0, "end": 4.5, "text": "logo antes do corte"},
            {"start": 15.0, "end": 17.0, "text": "minha fala"},
        ]

    @pytest.mark.parametrize(
        ("start", "end", "expected", "change"),
        [
            (15.0, 30.0, (15.0, 18.0), "início cortado em 15.0s"),
            (10.0, 16.0, (12.0, 16.0), "fim cortado em 16.0s"),
            (13.0, 16.0, (13.0, 16.0), "início e fim cortados (13.0s–16.0s)"),
        ],
    )
    def test_override_parcial_e_cortado_na_borda_sem_confirmar(self, client, scenario, edit_body, start, end, expected, change):
        response = client.put(scenario.edit_url, json=edit_body(start=start, end=end, lines=[(12.0, 18.0, "parcial")]))

        assert response.status_code == 200, response.text
        body = response.json()
        assert lines_of(body) == [(*expected, "parcial")]
        assert body["adjustments"] == [
            {"kind": "caption", "start": expected[0], "end": expected[1], "text": "parcial", "change": change}
        ]

    @pytest.mark.parametrize("line", [(11.0, 13.0, "sumiu"), (13.0, 15.0, "encostada")])
    def test_override_totalmente_fora_e_perda(self, client, scenario, edit_body, line):
        payload = edit_body(start=15.0, lines=[line, (20.0, 22.0, "fica")])

        response = client.put(scenario.edit_url, json=payload)

        assert_error(response, 409, "losses_require_confirmation")
        assert detail(response)["losses"] == [{"kind": "caption", "start": line[0], "end": line[1], "text": line[2]}]

        confirmed = client.put(scenario.edit_url, json={**payload, "confirm_losses": True})
        assert lines_of(confirmed.json()) == [(20.0, 22.0, "fica")]

    def test_janela_crescendo_preenche_os_dois_lados_com_whisper(self, client, scenario, edit_body):
        response = client.put(scenario.edit_url, json=edit_body(start=5.0, end=36.0, lines=[(20.0, 22.0, "minha fala")]))

        assert response.status_code == 200, response.text
        body = response.json()
        assert lines_of(body) == [
            (7.0, 9.5, "logo antes do corte"),
            (20.0, 22.0, "minha fala"),
            (32.0, 36.0, "essa merda aqui fora"),
        ]
        assert len(body["adjustments"]) == 2

    def test_texto_digitado_na_area_nova_tem_prioridade_sobre_whisper(self, client, scenario, edit_body):
        response = client.put(scenario.edit_url, json=edit_body(start=5.0, end=30.0, lines=[(7.5, 9.0, "digitada")]))

        assert lines_of(response.json()) == [(7.5, 9.0, "digitada")]

    def test_fala_que_so_encosta_na_area_nova_nao_entra(self, client, scenario, edit_body):
        # Área nova [5, 10]: só 1s dos 4s de [2, 6] está nela (abaixo de 50%).
        response = client.put(scenario.edit_url, json=edit_body(start=5.0, end=30.0, lines=[(20.0, 22.0, "x")]))

        assert (2.0, 6.0, "bem antes do corte") not in lines_of(response.json())
        assert all("bem antes" not in text for _, _, text in lines_of(response.json()))

    def test_janela_encolhendo_em_manual_nao_adiciona_nada(self, client, scenario, edit_body):
        response = client.put(scenario.edit_url, json=edit_body(start=14.0, end=26.0, lines=[(15.0, 18.0, "a"), (20.0, 24.0, "b")]))

        assert response.status_code == 200, response.text
        body = response.json()
        assert lines_of(body) == [(15.0, 18.0, "a"), (20.0, 24.0, "b")]
        assert body["adjustments"] == []

    @pytest.mark.parametrize(
        ("start", "end", "expected_texts"),
        [
            (14.0, 26.0, ["que porra é essa", "isso é incrível"]),
            (5.0, 36.0, [
                "logo antes do corte", "olá pessoal tudo bem", "que porra é essa",
                "isso é incrível", "vamos continuar agora", "essa merda aqui fora",
            ]),
        ],
    )
    def test_modo_auto_recalcula_pela_janela_nova(self, client, scenario, edit_body, read_clip, start, end, expected_texts):
        response = client.put(scenario.edit_url, json=edit_body(start=start, end=end))

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["draft"]["captions"]["mode"] == "auto"
        assert [text for _, _, text in lines_of(body)] == expected_texts
        assert body["adjustments"] == []
        assert read_clip(scenario.clip_id).get_caption_segments_override() is None

    def test_zoom_cria_ponto_interpolado_na_borda(self, client, scenario, edit_body):
        payload = edit_body(start=15.0, pan=[(10.0, 0.0), (20.0, 1.0)], confirm_losses=True)

        body = client.put(scenario.edit_url, json=payload).json()

        assert body["draft"]["pan"] == [{"t": 15.0, "cx": 0.5}, {"t": 20.0, "cx": 1.0}]
        assert body["adjustments"] == [
            {"kind": "pan", "start": 15.0, "end": 15.0, "change": "ponto interpolado criado na borda (15.0s)"}
        ]

    def test_zoom_exatamente_na_borda_conta_como_dentro(self, client, scenario, edit_body):
        response = client.put(scenario.edit_url, json=edit_body(start=15.0, end=25.0, pan=[(15.0, 0.1), (25.0, 0.9)]))

        assert response.status_code == 200, response.text
        assert response.json()["draft"]["pan"] == [{"t": 15.0, "cx": 0.1}, {"t": 25.0, "cx": 0.9}]
        assert response.json()["adjustments"] == []

    def test_removido_parcial_e_cortado_e_vira_cut_segments(self, client, scenario, edit_body, read_clip):
        body = client.put(scenario.edit_url, json=edit_body(start=15.0, removed=[(12.0, 18.0)])).json()

        assert body["draft"]["removed"] == [{"start": 15.0, "end": 18.0}]
        assert body["adjustments"][0]["change"] == "início cortado em 15.0s"
        clip = read_clip(scenario.clip_id)
        assert clip.get_manual_removed_ranges() == [(0.0, 3.0)]
        assert clip.get_cut_segments() == [(3.0, 15.0)]

    def test_removidos_sao_ordenados_e_mesclados(self, client, scenario, edit_body):
        body = client.put(scenario.edit_url, json=edit_body(removed=[(20.0, 22.0), (21.0, 24.0), (12.0, 13.0)])).json()

        assert body["draft"]["removed"] == [{"start": 12.0, "end": 13.0}, {"start": 20.0, "end": 24.0}]


# --------------------------------------------------------------------------
# Censura
# --------------------------------------------------------------------------


class TestCensura:
    def test_legenda_automatica_e_censurada_em_todas_as_saidas(self, client, make_clip, edit_body, fake_render, read_clip):
        sc = make_clip(profanity="censor")

        response = client.put(sc.edit_url, json=edit_body())

        assert response.status_code == 200, response.text
        assert "porra" not in response.text
        assert (15.0, 18.0, "que ***** é essa") in lines_of(response.json())
        assert "que ***** é essa" in fake_render.last.srt_text
        assert "porra" not in fake_render.last.srt_text
        assert "porra" not in read_clip(sc.clip_id).transcript_text

    def test_modo_censor_grava_mute_e_nao_corta(self, client, make_clip, edit_body, fake_render, read_clip):
        sc = make_clip(profanity="censor")

        body = client.put(sc.edit_url, json=edit_body()).json()

        assert body["draft"]["profanity"] == {
            "mode": "censor", "ranges": [{"start": 15.55, "end": 16.15, "kind": "muted"}],
        }
        clip = read_clip(sc.clip_id)
        assert clip.get_mute_ranges() == [pytest.approx((5.55, 6.15))]
        assert clip.get_cut_segments() is None
        assert fake_render.last.kwargs["mute_ranges"] == [pytest.approx((5.55, 6.15))]

    def test_modo_cut_abre_buraco_e_nao_grava_mute(self, client, make_clip, edit_body, read_clip):
        sc = make_clip(profanity="cut")

        response = client.put(sc.edit_url, json=edit_body())

        body = response.json()
        assert "porra" not in response.text
        assert body["draft"]["profanity"] == {
            "mode": "cut", "ranges": [{"start": 15.55, "end": 16.15, "kind": "cut"}],
        }
        clip = read_clip(sc.clip_id)
        assert clip.get_mute_ranges() is None
        assert clip.get_cut_segments() == [pytest.approx((0.0, 5.55)), pytest.approx((6.15, 20.0))]
        # O buraco do filtro não é trecho removido pelo usuário.
        assert clip.get_manual_removed_ranges() is None
        assert body["draft"]["removed"] == []

    @pytest.mark.parametrize("mode", ["censor", "cut"])
    def test_texto_digitado_nunca_e_censurado(self, client, make_clip, edit_body, fake_render, read_clip, mode):
        sc = make_clip(profanity=mode)

        response = client.put(sc.edit_url, json=edit_body(lines=[(11.0, 14.0, "que porra linda")]))

        assert response.status_code == 200, response.text
        assert lines_of(response.json()) == [(11.0, 14.0, "que porra linda")]
        assert "que porra linda" in fake_render.last.srt_text
        clip = read_clip(sc.clip_id)
        assert clip.get_caption_segments_override()[0]["text"] == "que porra linda"
        assert clip.transcript_text == "que porra linda"

    def test_area_nova_censura_whisper_e_preserva_o_digitado(self, client, make_clip, edit_body):
        sc = make_clip(profanity="censor")

        response = client.put(sc.edit_url, json=edit_body(end=36.0, lines=[(20.0, 22.0, "porra minha")]))

        assert lines_of(response.json()) == [(20.0, 22.0, "porra minha"), (32.0, 36.0, "essa ***** aqui fora")]

    def test_filtro_desligado_nao_censura(self, client, scenario, edit_body, read_clip):
        body = client.put(scenario.edit_url, json=edit_body()).json()

        assert (15.0, 18.0, "que porra é essa") in lines_of(body)
        assert body["draft"]["profanity"] == {"mode": "off", "ranges": []}
        assert read_clip(scenario.clip_id).get_mute_ranges() is None

    def test_palavrao_que_sai_da_janela_nao_e_perda(self, client, make_clip, edit_body):
        sc = make_clip(profanity="censor")

        response = client.put(sc.edit_url, json=edit_body(start=20.0))

        assert response.status_code == 200, response.text
        assert response.json()["draft"]["profanity"]["ranges"] == []


# --------------------------------------------------------------------------
# Validação, ordem das checagens e formato da saída
# --------------------------------------------------------------------------


class TestValidacao:
    @pytest.mark.parametrize(
        "overrides",
        [
            {"start": 20.0, "end": 10.0},
            {"start": 20.0, "end": 20.0},
            {"start": -1.0, "end": 10.0},
            {"start": 100.0, "end": 121.0},
            {"start": 10.0, "end": 10.4},
            {"removed": [(15.0, 15.0)]},
        ],
    )
    def test_invalid_range(self, client, scenario, edit_body, snapshot, overrides):
        before = snapshot(scenario)
        assert_error(client.put(scenario.edit_url, json=edit_body(**overrides)), 400, "invalid_range")
        assert snapshot(scenario) == before

    @pytest.mark.parametrize(
        ("lines", "index"),
        [
            ([(11.0, 12.0, "ok"), (13.0, 14.0, "   ")], 1),
            ([(12.0, 12.0, "tempo ruim")], 0),
            ([], None),
        ],
    )
    def test_invalid_caption(self, client, scenario, edit_body, lines, index):
        response = client.put(scenario.edit_url, json=edit_body(lines=lines))
        assert_error(response, 400, "invalid_caption")
        assert detail(response).get("index") == index

    @pytest.mark.parametrize(
        ("pan", "index"),
        [
            ([(12.0, 1.5)], 0),
            ([(12.0, -0.1)], 0),
            ([(12.0, 0.2), (12.0, 0.8)], 1),
        ],
    )
    def test_invalid_pan(self, client, scenario, edit_body, pan, index):
        response = client.put(scenario.edit_url, json=edit_body(pan=pan))
        assert_error(response, 400, "invalid_pan")
        assert detail(response)["index"] == index

    def test_invalid_pan_em_tela_dividida(self, client, make_clip, edit_body):
        sc = make_clip(streamer_region="0,0,100,100")
        assert_error(client.put(sc.edit_url, json=edit_body(pan=[(12.0, 0.5)])), 400, "invalid_pan")

    @pytest.mark.parametrize(
        ("style", "field"),
        [
            ({"color": "vermelho"}, "color"),
            ({"font_size": 99}, "font_size"),
            ({"preset": "neon"}, "preset"),
            ({"font_family": "Fonte Inexistente"}, "font_family"),
        ],
    )
    def test_invalid_style(self, client, scenario, edit_body, style, field):
        response = client.put(scenario.edit_url, json=edit_body(style=style))
        assert_error(response, 400, "invalid_style")
        assert detail(response)["field"] == field

    def test_nothing_left(self, client, scenario, edit_body, snapshot):
        before = snapshot(scenario)
        assert_error(client.put(scenario.edit_url, json=edit_body(removed=[(10.0, 29.8)])), 400, "nothing_left")
        assert snapshot(scenario) == before

    def test_clipe_inexistente(self, client, edit_body):
        assert_error(client.put(f"/clips/{uuid.uuid4().hex}/edit", json=edit_body()), 404, "not_found")

    def test_video_original_ausente(self, client, make_clip, edit_body):
        sc = make_clip(video_exists=False)
        assert_error(client.put(sc.edit_url, json=edit_body()), 404, "not_found")

    def test_clip_not_rendered(self, client, make_clip, edit_body):
        sc = make_clip(rendered=False)
        assert_error(client.put(sc.edit_url, json=edit_body()), 409, "clip_not_rendered")

    def test_validacao_vem_antes_do_404(self, client, edit_body):
        response = client.put(f"/clips/{uuid.uuid4().hex}/edit", json=edit_body(start=20.0, end=10.0))
        assert_error(response, 400, "invalid_range")

    def test_nothing_left_vem_antes_das_perdas(self, client, scenario, edit_body):
        payload = edit_body(start=15.0, lines=[(11.0, 13.0, "fora")], removed=[(15.0, 29.9)])
        assert_error(client.put(scenario.edit_url, json=payload), 400, "nothing_left")

    def test_revision_conflict_vem_antes_das_perdas(self, client, scenario, edit_body):
        payload = edit_body(revision=3, start=15.0, lines=[(11.0, 13.0, "fora")])
        assert_error(client.put(scenario.edit_url, json=payload), 409, "revision_conflict")

    @pytest.mark.parametrize("field", ["revision", "range", "caption_style", "captions", "pan", "removed"])
    def test_campo_obrigatorio_ausente_e_422(self, client, scenario, edit_body, field):
        payload = edit_body()
        del payload[field]
        response = client.put(scenario.edit_url, json=payload)
        assert response.status_code == 422
        assert isinstance(detail(response), list)

    def test_tipo_errado_e_422(self, client, scenario, edit_body):
        assert client.put(scenario.edit_url, json={**edit_body(), "revision": "abc"}).status_code == 422


class TestSaida:
    def test_formato_da_resposta_renderizada(self, client, scenario, edit_body):
        body = client.put(scenario.edit_url, json=edit_body(style={"color": "#FF0000"})).json()

        assert set(body) == {"status", "clip", "draft", "adjustments", "render_seconds"}
        assert body["clip"]["caption_style"]["color"] == "#FF0000"
        assert body["draft"]["caption_style"]["color"] == "#FF0000"
        assert body["clip"]["processed_path"].endswith("clip_0_r1.mp4")

    def test_grava_as_cinco_colunas_de_estilo(self, client, scenario, edit_body, read_clip, fake_render):
        style = {"enabled": False, "font_size": 12, "color": "#00FF00", "preset": "boxed", "font_family": "DejaVu Sans"}

        client.put(scenario.edit_url, json=edit_body(style=style))

        clip = read_clip(scenario.clip_id)
        assert (
            clip.caption_style_enabled, clip.caption_style_font_size, clip.caption_style_color,
            clip.caption_style_preset, clip.caption_style_font_family,
        ) == (False, 12, "#00FF00", "boxed", "DejaVu Sans")
        assert fake_render.last.kwargs["captions_enabled"] is False
        assert fake_render.last.srt_text is None

    def test_tempos_da_resposta_tem_tres_casas_e_banco_guarda_o_bruto(self, client, scenario, edit_body, read_clip):
        body = client.put(scenario.edit_url, json=edit_body(start=10.12345, end=29.98765)).json()

        assert body["draft"]["range"] == {"start": 10.123, "end": 29.988}
        assert (body["clip"]["start_time"], body["clip"]["end_time"]) == (10.123, 29.988)
        clip = read_clip(scenario.clip_id)
        assert (clip.start_time, clip.end_time) == (10.12345, 29.98765)

    def test_recalcula_texto_hashtags_e_titulos(self, client, scenario, edit_body, read_clip):
        client.put(scenario.edit_url, json=edit_body(lines=[(11.0, 14.0, "texto bem novo aqui")]))

        clip = read_clip(scenario.clip_id)
        assert clip.transcript_text == "texto bem novo aqui"
        assert json.loads(clip.hashtags)
        assert json.loads(clip.suggested_titles)
