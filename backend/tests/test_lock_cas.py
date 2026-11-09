"""Lock por clipe e compare-and-swap: contrato §0.5 e spec §5."""
import threading
from concurrent.futures import ThreadPoolExecutor

ERROR_ENVELOPE_CODE = "render_in_progress"


def test_dois_puts_simultaneos_um_200_e_um_409(client, scenario, edit_body, render_gate, fake_render, read_clip):
    barrier = threading.Barrier(2)
    responses = {}
    loser_done = threading.Event()

    def put(name):
        barrier.wait(10)
        response = client.put(scenario.edit_url, json=edit_body(start=12.0))
        responses[name] = response
        if response.status_code == 409:
            loser_done.set()
        return response

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(put, "a"), pool.submit(put, "b")]
        assert render_gate.entered.wait(10)
        # O render só é liberado depois que o perdedor já respondeu: garante
        # que as duas requisições realmente se cruzaram.
        assert loser_done.wait(10), "nenhuma requisição recebeu 409"
        render_gate.release.set()
        statuses = sorted(f.result(30).status_code for f in futures)

    assert statuses == [200, 409]
    loser = next(r for r in responses.values() if r.status_code == 409)
    assert loser.json()["detail"]["code"] == ERROR_ENVELOPE_CODE
    assert len(fake_render.calls) == 1
    clip = read_clip(scenario.clip_id)
    assert clip.revision == 1
    assert clip.processed_path.endswith("clip_0_r1.mp4")


def test_clipes_diferentes_nao_se_bloqueiam(client, make_clip, edit_body, fake_render):
    first, second = make_clip(), make_clip()
    # Cada render espera o outro entrar: só passa se os dois rodarem ao mesmo tempo.
    both_rendering = threading.Barrier(2)
    fake_render.hook = lambda _call: both_rendering.wait(10)

    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(client.put, sc.edit_url, json=edit_body()) for sc in (first, second)]
        statuses = [f.result(30).status_code for f in futures]

    assert statuses == [200, 200]


def test_edit_em_andamento_bloqueia_endpoints_antigos(client, scenario, edit_body, render_gate):
    with ThreadPoolExecutor(1) as pool:
        running = pool.submit(client.put, scenario.edit_url, json=edit_body())
        assert render_gate.entered.wait(10)

        responses = [
            client.post(scenario.recut_url, json={"start_time": 12.0}),
            client.put(f"/clips/{scenario.clip_id}/pan", json={"keyframes": [{"t": 1.0, "cx": 0.5}]}),
            client.put(f"/clips/{scenario.clip_id}/captions", json={"segments": [{"start": 1.0, "end": 2.0, "text": "oi"}]}),
            client.put(f"/clips/{scenario.clip_id}/cuts", json={"keep_segments": [[0.0, 5.0], [8.0, 20.0]]}),
        ]

        render_gate.release.set()
        assert running.result(30).status_code == 200

    for response in responses:
        assert response.status_code == 409, response.text
        assert response.json()["detail"]["code"] == ERROR_ENVELOPE_CODE


def test_compare_and_swap_detecta_gravacao_durante_o_render(client, scenario, edit_body, fake_render, read_clip, set_revision):
    # Simula um segundo processo da API, que o lock em memória não enxerga.
    fake_render.hook = lambda _call: set_revision(scenario.clip_id, 5)

    response = client.put(scenario.edit_url, json=edit_body(start=12.0))

    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "revision_conflict"
    assert response.json()["detail"]["current_revision"] == 5
    clip = read_clip(scenario.clip_id)
    assert clip.revision == 5
    assert clip.start_time == 10.0
    assert clip.processed_path == str(scenario.clip_path)
    assert sorted(p.name for p in scenario.output_dir.iterdir()) == ["clip_0.mp4"]
