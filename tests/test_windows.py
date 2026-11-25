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
    assert "--af-append=@noc:%s" % NIGHT_FILTER in night and not any(arg.startswith("--audio-spdif") for arg in night)
    assert not any(arg.startswith("--start") for arg in night)
    assert "--audio-channels=stereo" in build_args("mpv", stream, 0, {"mode": "stereo"})
    live = build_args("mpv", dict(stream, live=True), 300, {})
    assert not any(arg.startswith("--start") for arg in live)              # živé vysílání od živého okraje
    # vzhled titulků a zvuk/titulky naposledy zvolené u titulu
    styled = build_args("mpv", dict(stream, tracks={"key": "movie:11", "audio": "en", "subs": "off"}), 0, {},
                        style={"scale": 1.3, "rgb": "#FFE94D", "background": "shade"}, input_conf="/tmp/in.conf")
    assert "--sub-scale=1.30" in styled and "--sub-color=#FFE94D" in styled
    assert "--sub-border-style=background-box" in styled and "--sub-back-color=0.0/0.0/0.0/0.55" in styled
    assert "--alang=en,eng,cs,cze,ces,sk,slk,slo" in styled and "--sid=no" in styled
    assert "--input-conf=/tmp/in.conf" in styled
    czech = build_args("mpv", dict(stream, tracks={"audio": "", "subs": "sk"}), 0, {})
    assert "--slang=sk,slk,slo,cs,cze,ces,sk,slk,slo" in czech and "--sub-border-style=background-box" in czech


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


def test_background_listing_is_restarted_regularly_and_after_new_settings(served):
    """Plnění seznamu na pozadí: když se po otevření okna nerozběhlo (chybělo přihlášení), spustí se
    po změně nastavení a pak se pravidelně obnovuje – jako služba IndexService na televizi."""
    from promitarna_win.background import Keeper
    backend, url, api, _opened = served
    api.answers["index_keepalive"] = {"active": True}
    keeper = Keeper(backend.call, check_every=30, first_check=30)
    backend.keeper = keeper
    keeper.start()
    try:
        _post(url, "/api/home", backend.token, {})              # obyčejná akce nic nespouští
        time.sleep(0.2)
        assert [call for call in api.calls if call[0] == "index_keepalive"] == []
        _post(url, "/api/settings_set", backend.token, {"tmdb_key": "x"})
        for _ in range(50):
            if keeper.active:
                break
            time.sleep(0.05)
        assert keeper.active and ("index_keepalive", {}) in api.calls
    finally:
        keeper.stop()
        keeper.thread.join(2)
    assert not keeper.thread.is_alive()


def test_program_ends_soon_after_the_browser_window_is_closed(served):
    """Okno v Edge/prohlížeči ohlásí zavření: program skončí hned (nedrží soubory ve složce), ale
    obnovená stránka, která se zase ozve, ho neukončí."""
    from promitarna_win.app import CLOSE_GRACE, window_open
    backend, url, _api, _opened = served
    now = time.time()
    backend.alive = now - 1
    assert window_open(backend, now)
    assert _post(url, "/local/closed", backend.token, {})[1]["ok"]
    closed = backend.closed
    assert window_open(backend, closed + 1)                         # chvíli se čeká (obnovení stránky)
    assert not window_open(backend, closed + CLOSE_GRACE + 0.1)     # nikdo se neozval → konec
    backend.alive = closed + 0.5                                    # obnovená stránka se ozvala
    assert window_open(backend, closed + CLOSE_GRACE + 0.1)
    assert not window_open(backend, backend.alive + 60)             # okno se dlouho nehlásí → konec


FAKE_MPV_PANEL = textwrap.dedent("""
    import json, socket, sys, time
    path = [arg.split("=", 1)[1] for arg in sys.argv if arg.startswith("--input-ipc-server=")][0]
    log = open(sys.argv[-1], "a")                # adresa videa = soubor se záznamem příkazů
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(path)
    server.listen(1)
    connection, _ = server.accept()
    stream = connection.makefile("rwb", buffering=0)
    def send(message):
        stream.write((json.dumps(message) + "\\n").encode())
    send({"event": "client-message", "args": ["promitarna-menu"]})          # uživatel stiskl M
    tracks = [{"id": 1, "type": "audio", "lang": "cze", "title": "Dabing", "demux-channel-count": 6,
               "codec": "ac3", "selected": True},
              {"id": 2, "type": "audio", "lang": "eng", "selected": False},
              {"id": 1, "type": "sub", "lang": "cze", "external": True, "selected": False},
              {"id": 1, "type": "video", "selected": True}]
    for line in stream:
        message = json.loads(line.decode())
        command = message.get("command") or []
        log.write(json.dumps(command) + "\\n")
        log.flush()
        if command and command[0] == "quit":
            break
        if "request_id" in message:
            data = tracks if command[:2] == ["get_property", "track-list"] else None
            send({"request_id": message["request_id"], "error": "success", "data": data})
""")


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="náhradní mpv potřebuje unixový soket")
def test_player_panel_switches_tracks_and_subtitle_style(tmp_path):
    """Panel během přehrávání (klávesa M): stopy z mpv, přepnutí, vzhled titulků, posun zvuku, titulky ze souboru."""
    from promitarna_win.player import Player
    script = tmp_path / "fake_mpv.py"
    script.write_text(FAKE_MPV_PANEL, encoding="utf-8")
    log = tmp_path / "commands.log"
    launcher = tmp_path / "mpv"
    launcher.write_text("#!/bin/sh\nexec %s %s \"$@\"\n" % (sys.executable, script), encoding="utf-8")
    launcher.chmod(0o755)
    player = Player(lambda action, params: None, str(launcher), conf_dir=str(tmp_path / "data"))
    player.start({"url": str(log), "track": "1:pelisky:01", "title": "Pelíšky"}, 0, {}, {"scale": 1.0})
    for _ in range(50):
        if player.ipc is not None and player.status().get("menu"):
            break
        time.sleep(0.1)
    assert player.status()["menu"] == 1                                    # stisk M se dostal do aplikace
    conf = (tmp_path / "data" / "mpv-input.conf").read_text(encoding="utf-8")
    assert "m script-message promitarna-menu" in conf
    tracks = player.tracks()
    assert [track["title"] for track in tracks["audio"]] == ["Dabing · CZE · 6 kanálů · ac3", "ENG"]
    assert tracks["audio"][0]["selected"] and tracks["subs"][0]["title"] == "CZE · ze souboru"
    assert player.select("audio", 2) and player.select("subs", "no")
    assert player.style({"scale": 1.6, "rgb": "#FFE94D", "background": "outline"})
    player.audio_delay(150)
    assert player.add_subtitles("/tmp/cz.srt") and player.show(False)
    player.stop()
    commands = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    assert ["set_property", "aid", 2] in commands and ["set_property", "sid", "no"] in commands
    assert ["set_property", "sub-scale", "1.60"] in commands and ["set_property", "sub-color", "#FFE94D"] in commands
    assert ["set_property", "sub-border-style", "outline-and-shadow"] in commands
    assert ["set_property", "audio-delay", -0.15] in commands
    assert ["sub-add", "/tmp/cz.srt", "select"] in commands
    assert ["set_property", "window-minimized", True] in commands
