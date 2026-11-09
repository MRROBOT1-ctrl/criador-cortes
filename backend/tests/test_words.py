"""GET /jobs/{id}/words: contrato §3."""
import uuid

import pytest


def code_of(response):
    return response.json()["detail"]["code"]


LONG_TRANSCRIPT = {
    "language": "pt",
    "segments": [
        {"start": 10.0, "end": 11.0, "text": "cedo", "words": [{"start": 10.0, "end": 11.0, "text": " cedo"}]},
        {"start": 650.0, "end": 651.0, "text": "tarde", "words": [{"start": 650.0, "end": 651.0, "text": " tarde"}]},
    ],
}


class TestJanela:
    def test_sem_parametros_usa_o_padrao_limitado_ao_video(self, client, scenario):
        response = client.get(scenario.words_url)

        assert response.status_code == 200, response.text
        body = response.json()
        assert set(body) == {"job_id", "language", "from", "to", "truncated", "censored", "words"}
        assert (body["from"], body["to"], body["truncated"]) == (0.0, 120.0, False)
        assert body["job_id"] == scenario.job_id
        assert body["words"]

    def test_from_e_to_filtram_as_palavras(self, client, scenario):
        body = client.get(scenario.words_url, params={"from": 15, "to": 18}).json()

        assert (body["from"], body["to"]) == (15.0, 18.0)
        assert [w["text"] for w in body["words"]] == ["que", "porra", "é", "essa"]
        assert set(body["words"][0]) == {"start", "end", "text", "is_censored"}

    def test_palavra_parcialmente_na_janela_entra(self, client, scenario):
        body = client.get(scenario.words_url, params={"from": 15.8, "to": 16.3}).json()

        assert [w["text"] for w in body["words"]] == ["porra", "é"]

    def test_palavras_ordenadas_e_sem_espacos_nas_pontas(self, client, scenario):
        words = client.get(scenario.words_url).json()["words"]

        assert [w["start"] for w in words] == sorted(w["start"] for w in words)
        assert all(w["text"] == w["text"].strip() for w in words)

    def test_tempos_em_tres_casas(self, client, make_clip):
        transcript = {
            "language": "pt",
            "segments": [{"start": 1.0, "end": 2.0, "text": "oi", "words": [{"start": 1.23456, "end": 1.98765, "text": "oi"}]}],
        }
        words = client.get(make_clip(transcript=transcript).words_url).json()["words"]

        assert (words[0]["start"], words[0]["end"]) == (1.235, 1.988)

    @pytest.mark.parametrize(
        ("params", "expected"),
        [
            ({"from": -5, "to": 10}, (0.0, 10.0)),
            ({"from": 50, "to": 9999}, (50.0, 120.0)),
            ({"from": 110}, (110.0, 120.0)),
            ({"to": 40}, (0.0, 40.0)),
        ],
    )
    def test_ajuste_aos_limites_nao_conta_como_truncar(self, client, scenario, params, expected):
        body = client.get(scenario.words_url, params=params).json()

        assert (body["from"], body["to"]) == expected
        assert body["truncated"] is False

    def test_janela_acima_de_600s_e_truncada(self, client, make_clip):
        sc = make_clip(duration=1000.0, transcript=LONG_TRANSCRIPT)

        body = client.get(sc.words_url, params={"from": 0, "to": 900}).json()

        assert (body["from"], body["to"], body["truncated"]) == (0.0, 600.0, True)
        assert [w["text"] for w in body["words"]] == ["cedo"]

    def test_truncar_acontece_depois_de_ajustar_aos_limites(self, client, make_clip):
        sc = make_clip(duration=1000.0, transcript=LONG_TRANSCRIPT)

        body = client.get(sc.words_url, params={"from": 100, "to": 99999}).json()

        assert (body["from"], body["to"], body["truncated"]) == (100.0, 700.0, True)
        assert [w["text"] for w in body["words"]] == ["tarde"]

    def test_sem_to_em_video_longo_nao_marca_truncated(self, client, make_clip):
        sc = make_clip(duration=1000.0, transcript=LONG_TRANSCRIPT)

        body = client.get(sc.words_url).json()

        assert (body["from"], body["to"], body["truncated"]) == (0.0, 600.0, False)

    def test_exatamente_600s_nao_e_truncado(self, client, make_clip):
        sc = make_clip(duration=1000.0, transcript=LONG_TRANSCRIPT)

        assert client.get(sc.words_url, params={"from": 0, "to": 600}).json()["truncated"] is False


class TestCensura:
    @pytest.mark.parametrize("mode", ["censor", "cut"])
    def test_filtro_ligado_nao_vaza_texto_bruto(self, client, make_clip, mode):
        response = client.get(make_clip(profanity=mode).words_url, params={"from": 15, "to": 18})

        body = response.json()
        assert "porra" not in response.text
        assert body["censored"] is True
        assert [(w["text"], w["is_censored"]) for w in body["words"]] == [
            ("que", False), ("*****", True), ("é", False), ("essa", False),
        ]

    def test_filtro_desligado(self, client, scenario):
        body = client.get(scenario.words_url, params={"from": 15, "to": 18}).json()

        assert body["censored"] is False
        assert "porra" in [w["text"] for w in body["words"]]
        assert not any(w["is_censored"] for w in body["words"])


class TestErros:
    @pytest.mark.parametrize(
        "params",
        [
            {"from": 30, "to": 10},
            {"from": 30, "to": 30},
            {"from": 500, "to": 600},
            {"from": -10, "to": -5},
        ],
    )
    def test_invalid_range(self, client, scenario, params):
        response = client.get(scenario.words_url, params=params)

        assert response.status_code == 400
        assert code_of(response) == "invalid_range"

    @pytest.mark.parametrize("status", ["pending", "running"])
    def test_transcription_not_ready(self, client, make_clip, status):
        response = client.get(make_clip(transcript=None, job_status=status).words_url)

        assert response.status_code == 409
        assert code_of(response) == "transcription_not_ready"

    @pytest.mark.parametrize("status", ["completed", "failed"])
    def test_transcript_not_found(self, client, make_clip, status):
        response = client.get(make_clip(transcript=None, job_status=status).words_url)

        assert response.status_code == 404
        assert code_of(response) == "transcript_not_found"

    def test_job_inexistente(self, client):
        response = client.get(f"/jobs/{uuid.uuid4().hex}/words")

        assert response.status_code == 404
        assert code_of(response) == "not_found"

    def test_tipo_errado_e_422(self, client, scenario):
        assert client.get(scenario.words_url, params={"from": "abc"}).status_code == 422


class TestOutros:
    def test_transcript_antigo_sem_words_responde_lista_vazia(self, client, make_clip):
        transcript = {"language": "pt", "segments": [{"start": 1.0, "end": 2.0, "text": "oi"}]}

        response = client.get(make_clip(transcript=transcript).words_url)

        assert response.status_code == 200, response.text
        assert response.json()["words"] == []

    def test_transcript_em_andamento_ja_salvo_responde_200(self, client, make_clip):
        assert client.get(make_clip(job_status="running").words_url).status_code == 200

    def test_cache_control_so_no_200(self, client, make_clip, scenario):
        assert client.get(scenario.words_url).headers["cache-control"] == "private, max-age=86400"
        assert "cache-control" not in client.get(scenario.words_url, params={"from": 30, "to": 10}).headers
        assert "cache-control" not in client.get(make_clip(transcript=None).words_url).headers

    def test_language_vem_do_job_e_depois_do_transcript(self, client, make_clip):
        assert client.get(make_clip(job_language="en").words_url).json()["language"] == "en"
        assert client.get(make_clip(job_language=None).words_url).json()["language"] == "pt"
