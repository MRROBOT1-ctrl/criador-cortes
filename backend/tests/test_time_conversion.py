"""Conversão absoluto <-> relativo: funções puras e ida e volta pela API."""
import random

import pytest

from app.services import clip_edit


@pytest.mark.parametrize(
    ("absolute", "clip_start", "relative"),
    [(15.0, 10.0, 5.0), (10.0, 10.0, 0.0), (0.0, 0.0, 0.0), (12.5, 2.25, 10.25)],
)
def test_valores_conhecidos(absolute, clip_start, relative):
    assert clip_edit.to_relative(absolute, clip_start) == pytest.approx(relative)
    assert clip_edit.to_absolute(relative, clip_start) == pytest.approx(absolute)


def test_ida_e_volta_com_valores_arbitrarios():
    rng = random.Random(20261109)
    for _ in range(1000):
        absolute = rng.uniform(0.0, 14400.0)
        clip_start = rng.uniform(0.0, 14400.0)

        back = clip_edit.to_absolute(clip_edit.to_relative(absolute, clip_start), clip_start)

        assert back == pytest.approx(absolute, abs=1e-6)


def test_listas_de_intervalos_ida_e_volta():
    ranges = [(12.5, 13.75), (20.0, 21.125)]

    relative = clip_edit.ranges_to_relative(ranges, 10.0)

    assert relative == [pytest.approx((2.5, 3.75)), pytest.approx((10.0, 11.125))]
    assert clip_edit.ranges_to_absolute(relative, 10.0) == [pytest.approx(r) for r in ranges]


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1.2344, 1.234), (1.2346, 1.235), (2.0, 2.0), (0.0004, 0.0)],
)
def test_arredondamento_em_tres_casas(value, expected):
    assert clip_edit.r3(value) == expected


def test_put_absoluto_vira_relativo_no_banco_e_volta_absoluto_no_draft(client, make_clip, edit_body, read_clip):
    sc = make_clip(start=12.5, end=32.5)
    payload = edit_body(
        start=12.5, end=32.5,
        lines=[(14.25, 16.75, "legenda")],
        pan=[(20.5, 0.25), (30.5, 0.75)],
        removed=[(25.0, 26.5)],
    )

    response = client.put(sc.edit_url, json=payload)

    assert response.status_code == 200, response.text
    clip = read_clip(sc.clip_id)
    assert clip.get_caption_segments_override() == [{"start": 1.75, "end": 4.25, "text": "legenda"}]
    assert clip.get_manual_pan_keyframes() == [(8.0, 0.25), (18.0, 0.75)]
    assert clip.get_manual_removed_ranges() == [(12.5, 14.0)]

    draft = client.get(sc.draft_url).json()
    assert draft["captions"]["lines"] == payload["captions"]["lines"]
    assert draft["pan"] == payload["pan"]
    assert draft["removed"] == payload["removed"]


def test_reenviar_o_rascunho_varias_vezes_nao_acumula_erro(client, make_clip):
    sc = make_clip(
        start=10.333, end=30.667,
        override=[{"start": 1.111, "end": 3.222, "text": "legenda"}],
        pan_rel=[(2.5, 0.25), (9.75, 0.75)],
        removed_rel=[(12.125, 13.875)],
    )
    original = client.get(sc.draft_url).json()
    draft = original

    for _ in range(5):
        body = {key: draft[key] for key in ("revision", "range", "caption_style", "captions", "pan", "removed")}
        response = client.put(sc.edit_url, json=body)
        assert response.status_code == 200, response.text
        draft = response.json()["draft"]

    final = client.get(sc.draft_url).json()
    assert final["revision"] == 5
    for key in ("range", "captions", "pan", "removed"):
        assert final[key] == original[key], key


def test_mudar_o_corte_nao_move_o_conteudo_no_tempo_absoluto(client, make_clip, read_clip):
    sc = make_clip(override=[{"start": 10.0, "end": 12.0, "text": "fixa"}], pan_rel=[(10.0, 0.5)])

    draft = client.get(sc.draft_url).json()
    body = {key: draft[key] for key in ("revision", "caption_style", "captions", "pan", "removed")}
    response = client.put(sc.edit_url, json={**body, "range": {"start": 17.0, "end": 37.0}})

    assert response.status_code == 200, response.text
    moved = client.get(sc.draft_url).json()
    assert {"start": 20.0, "end": 22.0, "text": "fixa"} in moved["captions"]["lines"]
    assert moved["pan"] == [{"t": 20.0, "cx": 0.5}]
    assert read_clip(sc.clip_id).get_manual_pan_keyframes() == [(3.0, 0.5)]
