import base64
import threading
import time

from conftest import free_ports
from link.app import LinkApp
from link.bridge import Bridge
from link.handoff import Handoff
from link.launcher import DESKTOP_URL, Launcher
from link.page import render_page
from link.pairing import PairingBook
from link.protocol import parse_send
from link.server import LinkServer
from link.tab import TabController

KEY = "bridge-key-1"


def wait_until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def call(bridge, id_, method, params=None, key=KEY):
    message = {"type": "takumion-link", "id": id_, "method": method, "key": key}
    if params is not None:
        message["params"] = params
    bridge.handle(message)


def test_bridge_answers_with_the_same_id(world):
    posted = []
    bridge = Bridge(world.service, posted.append, key=KEY)
    call(bridge, 7, "hello")
    assert posted[-1]["id"] == 7 and posted[-1]["result"]["paired"] is True
    call(bridge, 8, "links", {"doc": "prj_1"})
    assert posted[-1]["result"]["bodies"] == []
    call(bridge, 9, "nope")
    assert posted[-1]["error"]["code"] == "bad-request"


def test_bridge_refuses_a_page_without_the_key(world):
    """Orca loads any page into the tab with window.orca: only the page the plugin opened knows the key."""
    posted = []
    bridge = Bridge(world.service, posted.append, key=KEY)
    for wrong in ("", "nope", None):
        call(bridge, 1, "hello", key=wrong)
        assert posted[-1]["error"]["code"] == "forbidden" and "result" not in posted[-1]
    keyless = Bridge(world.service, posted.append)
    call(keyless, 2, "hello")
    assert posted[-1]["error"]["code"] == "forbidden", "a bridge with no key answers nobody"


def send_params():
    return {"doc": {"key": "prj_1", "name": "B"}, "mode": "add", "format": "stl",
            "parts": [{"bodyKey": "body_1", "name": "Base", "data": base64.b64encode(b"x").decode()}]}


def test_bridge_send_runs_off_the_calling_thread(world):
    posted = []
    caller = threading.current_thread()
    seen = []
    original = world.service.send
    world.service.send = lambda req: (seen.append(threading.current_thread()), original(req))[1]
    call(Bridge(world.service, posted.append, key=KEY), 1, "send", send_params())
    assert wait_until(lambda: posted)
    assert seen[0] is not caller
    assert posted[0]["result"]["results"][0]["action"] == "added"


def test_bridge_runs_one_send_at_a_time(world):
    posted = []
    gate = threading.Event()
    original = world.service.send
    world.service.send = lambda req: (gate.wait(5), original(req))[1]
    bridge = Bridge(world.service, posted.append, key=KEY)
    call(bridge, 1, "send", send_params())
    call(bridge, 2, "send", send_params())
    assert posted[-1]["id"] == 2 and posted[-1]["error"]["code"] == "orca-busy"
    gate.set()
    assert wait_until(lambda: any(p["id"] == 1 and "result" in p for p in posted))
    call(bridge, 3, "send", send_params())
    assert wait_until(lambda: any(p["id"] == 3 and "result" in p for p in posted)), "free again after the first ends"


def test_bridge_internal_errors_name_no_path(world):
    posted, logged = [], []
    world.service.links = lambda doc: (_ for _ in ()).throw(OSError(13, "Permission denied", "/home/someone/x"))
    call(Bridge(world.service, posted.append, key=KEY, log=logged.append), 1, "links", {"doc": "p"})
    assert posted[-1]["error"]["code"] == "internal"
    assert "/home" not in posted[-1]["error"]["message"] and "/home/someone" in logged[-1]


def make_tab(world, tmp_path):
    posted, opened = [], []
    pairing = PairingBook(tmp_path / "pairings.json")
    server = LinkServer(world.service, pairing, free_ports(1), log=lambda line: None)
    tab = TabController(service=world.service, pairing=pairing, server=server, launcher=Launcher(opener=opened.append),
                        post=posted.append, bridge_key=KEY, log=lambda line: None)
    return tab, pairing, server, posted, opened


def press(tab, action, **extra):
    tab.handle({"type": "tab", "nonce": tab.nonce, "action": action, **extra})


def test_tab_pairing_flow(world, tmp_path):
    tab, pairing, _, posted, _ = make_tab(world, tmp_path)
    press(tab, "ready")
    assert posted[-1]["type"] == "tab-state" and posted[-1]["state"]["code"] is None
    press(tab, "pair")
    code = posted[-1]["state"]["code"]
    assert len(code["value"]) == 6 and 0 < code["secondsLeft"] <= 120
    pairing.pair(code["value"], "https://app.takumion.com", "Chrome")
    state = posted[-1]["state"]
    assert state["code"] is None and state["pairings"][0]["clientName"] == "Chrome"
    press(tab, "revoke", id=state["pairings"][0]["id"])
    assert posted[-1]["state"]["pairings"] == []


def test_tab_ignores_pages_without_its_nonce(world, tmp_path):
    """Another page in the tab can neither press the tab's buttons nor see the pairing code."""
    tab, pairing, _, posted, _ = make_tab(world, tmp_path)
    for nonce in (None, "", "guess"):
        tab.handle({"type": "tab", "nonce": nonce, "action": "pair"})
    assert pairing.live_code() is None and posted == []
    pairing.start_code()
    assert posted == [], "nothing is pushed before the plugin's own page spoke"


def test_tab_stops_pushing_after_leave(world, tmp_path):
    tab, pairing, _, posted, _ = make_tab(world, tmp_path)
    press(tab, "ready")
    count = len(posted)
    press(tab, "leave")
    pairing.start_code()
    world.service.dismiss_setup_card()
    assert len(posted) == count, "once the page navigated away, state (and any code) stays in the plugin"
    press(tab, "ready")
    assert posted[-1]["state"]["code"] is not None, "back on the plugin's page, the state is pushed again"


def test_tab_state_shows_server_and_setup(world, tmp_path):
    tab, _, server, posted, opened = make_tab(world, tmp_path)
    server.start()
    try:
        state = tab.state()
        assert state["server"] == {"port": server.port, "error": None}
        assert state["setup"] == {"loadGeometryOnly": "unknown", "showCard": False}, "no card before a 3MF fails"
        assert state["takumionHere"] == f"https://app.takumion.com/?host=orca#orcaLinkKey={KEY}"
        assert "takumionDev" not in state, "no hard-coded dev servers are offered"
        world.orca.behaviour = "ignore"
        world.service.send(parse_send({"doc": {"key": "p", "name": "P"}, "mode": "add", "format": "3mf",
                                       "parts": [{"bodyKey": "b", "name": "B", "data": "AA=="}]}))
        assert tab.state()["setup"]["showCard"] is True
        press(tab, "dismiss-setup")
        assert posted[-1]["state"]["setup"]["showCard"] is False
        press(tab, "open-takumion", target="desktop")
        assert opened == [DESKTOP_URL] and DESKTOP_URL in posted[-1]["state"]["notice"]
        press(tab, "open-takumion", target="nowhere")
        assert "Could not open" in posted[-1]["state"]["notice"]
    finally:
        server.stop()


def test_hosted_url_and_bad_config():
    from link.launcher import WEB_URL, checked_web_url, hosted_url

    assert hosted_url("http://localhost:5180/") == "http://localhost:5180/?host=orca"
    assert hosted_url("https://app.takumion.com/?project=p&host=x", "k") == "https://app.takumion.com/?project=p&host=orca#orcaLinkKey=k"
    assert checked_web_url("javascript:alert(1)") == WEB_URL
    assert checked_web_url(None) == WEB_URL
    assert checked_web_url("http://evil.example/") == WEB_URL, "plain http only on this computer"
    assert checked_web_url("http://127.0.0.1:5180/") == "http://127.0.0.1:5180/"
    assert checked_web_url("https://cad.example.com/") == "https://cad.example.com/"
    assert Launcher("http://localhost:5180/").here_url("k") == "http://localhost:5180/?host=orca#orcaLinkKey=k"


def test_the_page_carries_the_nonce_escaped():
    html = render_page(nonce='a"b<c')
    assert '<meta name="takumion-tab" content="a&quot;b&lt;c">' in html
    assert '<meta name="takumion-tab"' not in render_page(), "no nonce, no tag"


def test_bridge_hello_names_the_tab_page(world):
    posted = []
    call(Bridge(world.service, posted.append, lambda: "http://127.0.0.1:47390/tab", key=KEY), 1, "hello")
    assert posted[-1]["result"]["tab"] == {"url": "http://127.0.0.1:47390/tab"}
    call(Bridge(world.service, posted.append, key=KEY), 2, "hello")
    assert "tab" not in posted[-1]["result"]


def test_refresh_pushes_only_when_the_view_changes(world, tmp_path):
    tab, _, _, posted, _ = make_tab(world, tmp_path)
    press(tab, "refresh")
    world.plate.refresh([], "", "PageMessage")
    world.plate.refresh([], "", "PageMessage")
    assert len(posted) <= 1, "an unchanged plate pushes nothing"
    base = len(posted)
    world.orca.objects = []
    from link.plate import PlateObject
    world.plate.refresh([PlateObject(1, "x", "/elsewhere.stl", (1.0, 1.0))], "", "PageMessage")
    assert len(posted) == base, "objects that are not linked do not change the view"


class FakePanel:
    """What orca.host.ui.create_window returns, recording what reaches the page."""

    def __init__(self, html, on_message, on_close):
        self.html, self.on_message, self.on_close = html, on_message, on_close
        self.posted, self.open, self.shown = [], True, 0

    def post(self, data):
        self.posted.append(data)

    def show(self):
        self.shown += 1

    def is_open(self):
        return self.open

    def close(self):
        self.open = False
        self.on_close()


def make_app(world, data_dir, panels):
    def host(html, on_message, on_close):
        panels.append(FakePanel(html, on_message, on_close))
        return panels[-1]

    tab_posts = []
    app = LinkApp(data_dir, notifier=world.notes, post=tab_posts.append, handoff=Handoff(data_dir, world.orca.deliver),
                  launcher=Launcher(opener=lambda url: None), ports=free_ports(1), log=lambda line: None, panel_host=host)
    return app, tab_posts


def test_the_plugin_window_opens_once_and_shows_the_plugin_page(world, data_dir):
    panels = []
    app, tab_posts = make_app(world, data_dir, panels)
    assert app.show_panel() is True and len(panels) == 1
    panel = panels[0]
    assert '<meta name="takumion-view" content="panel">' in panel.html and app.tab.nonce in panel.html
    assert '<meta name="takumion-open"' not in panel.html, "the panel never jumps to Takumion"
    assert app.show_panel() is True and len(panels) == 1 and panel.shown == 1, "a second call shows the same panel"

    panel.on_message({"type": "tab", "nonce": app.tab.nonce, "action": "pair"})
    assert panel.posted[-1]["state"]["code"] is not None, "the panel shows the pairing code"
    assert tab_posts == [], "the tab (Takumion) is never sent the state"

    panel.on_message({"type": "tab", "nonce": "wrong", "action": "cancel-pair"})
    assert app.pairing.live_code() is not None, "the panel checks the nonce too"

    panel.close()
    count = len(panel.posted)
    app.pairing.cancel_code()
    assert len(panel.posted) == count, "a closed panel is not posted to"
    assert app.show_panel() is True and len(panels) == 2, "closed, it opens again"
    app.stop()
    assert panels[1].open is False, "the plugin closes its panel when it unloads"


def test_bridge_show_panel_opens_it_and_says_whether_it_could(world, data_dir):
    panels = []
    app, tab_posts = make_app(world, data_dir, panels)
    key = app.bridge._key
    app.bridge.handle({"type": "takumion-link", "id": 1, "key": key, "method": "show-panel"})
    assert tab_posts[-1] == {"type": "takumion-link", "id": 1, "result": {"shown": True}} and len(panels) == 1
    app.bridge.handle({"type": "takumion-link", "id": 2, "key": "nope", "method": "show-panel"})
    assert tab_posts[-1]["error"]["code"] == "forbidden" and len(panels) == 1
    posts = []
    no_panels = Bridge(world.service, posts.append, key=KEY)
    call(no_panels, 3, "show-panel")
    assert posts[-1]["result"] == {"shown": False}, "an Orca without panels says so; Takumion falls back to the tab page"
