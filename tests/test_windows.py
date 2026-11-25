"""Windows verze (``windows/``): server okna, přehrávač mpv a stahování – bez Windows a bez mpv.

Okno samo (JavaScript) se tu nezkouší; jeho vstupy a výstupy jsou stejné akce logiky jako na televizi,
které zkouší ``test_app_api.py``. Tady jde o to, co je ve Windows verzi navíc.
"""

import http.server
import json
import os
import socket
import sys
import textwrap
import threading
import time
import urllib.request

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "windows"))


class FakeApi(object):
    """Logika aplikace naoko: zapisuje, co se volalo, a vrací připravené odpovědi."""

    def __init__(self, answers=None):
        self.calls = []
        self.answers = answers or {}

    def call(self, action, params_json="{}"):
        params = json.loads(params_json)
        self.calls.append((action, params))
        answer = self.answers.get(action)
        if callable(answer):
            answer = answer(params)
        return json.dumps({"ok": True, "data": answer if answer is not None else {}})


OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))   # jiné testy mění výchozí urlopen


def _get(url):
    return OPENER.open(url, timeout=5)


def _post(url, path, token, params):
    request = urllib.request.Request(url.split("/?")[0] + path, data=json.dumps(params).encode("utf-8"),
                                     headers={"Content-Type": "application/json", "X-Token": token or ""})
    try:
        with OPENER.open(request, timeout=5) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


@pytest.fixture
def served(tmp_path):
    from promitarna_win.server import Backend, serve
    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text('<script>window.TOKEN = "__TOKEN__";</script>', encoding="utf-8")
    (ui / "app.js").write_text("// okno", encoding="utf-8")
    api = FakeApi({"home": {"sections": []}})
    opened = []
    backend = Backend(api, None, None, str(ui))
    server, url = serve(backend)
    import promitarna_win.server as module
    original = module.webbrowser.open
    module.webbrowser.open = opened.append
    yield backend, url, api, opened
    module.webbrowser.open = original
    server.shutdown()


def test_server_serves_the_window_and_calls_the_logic_only_with_the_token(served):
    backend, url, api, opened = served
    with _get(url) as response:            # adresa okna nese klíč
        assert backend.token in response.read().decode("utf-8")
    with pytest.raises(urllib.error.HTTPError):
        _get(url.split("?")[0])            # bez klíče se okno neukáže
    with _get(url.split("/?")[0] + "/ui/app.js") as response:
        assert response.read() == b"// okno"
    with pytest.raises(urllib.error.HTTPError):
        _get(url.split("/?")[0] + "/ui/../tests/x")   # mimo složku okna nic
    assert _post(url, "/api/home", "spatny", {})[0] == 403                 # jiná stránka v prohlížeči neprojde
    status, answer = _post(url, "/api/home", backend.token, {"x": 1})
    assert status == 200 and answer == {"ok": True, "data": {"sections": []}} and api.calls == [("home", {"x": 1})]
    _post(url, "/local/open", backend.token, {"url": "file:///C:/Windows"})   # otevře se jen webová adresa
    _post(url, "/local/open", backend.token, {"url": "https://www.youtube.com/watch?v=abc"})
    assert opened == ["https://www.youtube.com/watch?v=abc"]
    before = backend.alive
    time.sleep(0.01)
    _post(url, "/local/alive", backend.token, {})
    assert backend.alive > before                                          # okno se hlásí, že je otevřené
    assert _post(url, "/local/quit", backend.token, {})[1]["ok"] and backend.quit.is_set()


def test_mpv_command_line_follows_the_audio_settings():
    from promitarna_win.player import build_args, NIGHT_FILTER
    stream = {"url": "https://cdn.example/v.mkv", "title": "Pelíšky", "subtitles": ["https://cdn.example/cz.srt"],
              "headers": {"User-Agent": "Promitarna/1"}}
    args = build_args("mpv", stream, 125.6, {"mode": "auto", "delay_ms": 100}, "/tmp/ipc")
    assert args[0] == "mpv" and args[-1] == stream["url"] and "--start=125" in args
    assert "--user-agent=Promitarna/1" in args and "--sub-file=https://cdn.example/cz.srt" in args
    assert "--audio-spdif=ac3,eac3,dts,dts-hd,truehd" in args             # předat soundbaru
    assert "--audio-delay=-0.100" in args                                  # zvuk ze soundbaru zní později
    assert "--input-ipc-server=/tmp/ipc" in args
    night = build_args("mpv", stream, 0, {"mode": "auto", "night": True})
    assert "--af-append=%s" % NIGHT_FILTER in night and not any(arg.startswith("--audio-spdif") for arg in night)
    assert not any(arg.startswith("--start") for arg in night)
    assert "--audio-channels=stereo" in build_args("mpv", stream, 0, {"mode": "stereo"})
    live = build_args("mpv", dict(stream, live=True), 300, {})
    assert not any(arg.startswith("--start") for arg in live)              # živé vysílání od živého okraje


FAKE_MPV = textwrap.dedent('''
    import json, socket, sys, time
    path = [arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--input-ipc-server=")][0]
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    connection, _ = server.accept()
    stream = connection.makefile("rwb", buffering=0)
    for _ in range(3):
        stream.readline()                       # observe_property × 3
    def send(name, value):
        stream.write((json.dumps({"event": "property-change", "name": name, "data": value}) + "\\n").encode())
    send("duration", 1200.0)
    for position in (300.0, 610.5):
        send("time-pos", position)
        time.sleep(1.3)
    connection.close()
''')


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="náhradní mpv potřebuje unixový soket")
def test_player_saves_progress_and_reports_the_end(tmp_path):
    from promitarna_win.player import Player
    script = tmp_path / "fake_mpv.py"
    script.write_text(FAKE_MPV, encoding="utf-8")
    launcher = tmp_path / "mpv"
    launcher.write_text("#!/bin/sh\nexec %s %s \"$@\"\n" % (sys.executable, script), encoding="utf-8")
    launcher.chmod(0o755)
    saved = []
    player = Player(lambda action, params: saved.append((action, params)), str(launcher))
    state = player.start({"url": "https://cdn.example/v.mkv", "track": "1:pelisky:01", "choice": "movie:11",
                          "title": "Pelíšky"}, 300)
    assert state["playing"] and state["choice"] == "movie:11"
    for _ in range(100):
        if player.status().get("finished"):
            break
        time.sleep(0.1)
    status = player.status()
    assert status["finished"] and not status["playing"] and not status["ended"]   # skončil uprostřed
    assert saved and all(action == "progress_save" for action, _params in saved)
    last = saved[-1][1]
    assert last["track"] == "1:pelisky:01" and last["position"] == 610.5 and last["total"] == 1200.0
    assert not last["ended"]


def test_downloads_resume_a_partial_file_and_report_to_the_logic(tmp_path):
    from promitarna_win.downloads import Downloads
    payload = b"x" * 300000

    class Files(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            start = 0
            if self.headers.get("Range"):
                start = int(self.headers["Range"].split("=")[1].split("-")[0])
                self.send_response(206)
            else:
                self.send_response(200)
            body = payload[start:] if self.path == "/v.mp4" else b"1\n00:00:01,000 --> 00:00:02,000\nAhoj\n"
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    files = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Files)
    threading.Thread(target=files.serve_forever, daemon=True).start()
    base = "http://127.0.0.1:%d" % files.server_address[1]
    target = tmp_path / "Pelisky.mp4"
    (tmp_path / "Pelisky.mp4.part").write_bytes(payload[:100000])           # rozpracované z minula
    jobs = [{"vid": "1:pelisky:01", "title": "Pelíšky", "url": base + "/v.mp4", "headers": {}, "path": str(target),
             "part": str(target) + ".part", "total": 0,
             "subtitles": [{"url": base + "/cz.srt", "path": str(tmp_path / "Pelisky.1.srt")}]}]
    calls = []

    def call(action, params):
        calls.append(action)
        if action == "settings_get":
            return {"download_dir": str(tmp_path)}
        if action == "download_next":
            return jobs.pop(0) if jobs else {"vid": ""}
        if action == "download_finish":
            calls.append(params)
        return {}

    downloads = Downloads(call, str(tmp_path / "jinde"))
    downloads.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    downloads._run()
    files.shutdown()
    assert target.read_bytes() == payload and not os.path.exists(str(target) + ".part")   # navázalo, celé
    finish = calls[calls.index("download_finish") + 1]
    assert finish["size"] == len(payload) and finish["subtitles"] == [str(tmp_path / "Pelisky.1.srt")]
    assert "Staženo: Pelíšky" == downloads.message


def test_vector_icons_become_svg(tmp_path):
    sys.path.insert(0, os.path.join(ROOT, "windows"))
    import build_assets
    names = build_assets.build(str(tmp_path))
    assert "ic_tile_help" in names["icons"] and "logo.png" in names["art"]
    svg = (tmp_path / "ic_tile_help.svg").read_text(encoding="utf-8")
    assert svg.startswith("<svg") and 'stroke="currentColor"' in svg and "<path" in svg
    assert json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8")) == names
