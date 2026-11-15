import base64
import threading
import time

from conftest import free_ports
from link.bridge import Bridge
from link.launcher import DESKTOP_URL, Launcher
from link.pairing import PairingBook
from link.protocol import parse_send
from link.server import LinkServer
from link.tab import TabController


def wait_until(predicate, timeout=3.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


def test_bridge_answers_with_the_same_id(world):
    posted = []
    bridge = Bridge(world.service, posted.append)
    bridge.handle({"type": "takumion-link", "id": 7, "method": "hello"})
    assert posted[-1]["id"] == 7 and posted[-1]["result"]["paired"] is True
    bridge.handle({"type": "takumion-link", "id": 8, "method": "links", "params": {"doc": "prj_1"}})
    assert posted[-1]["result"]["bodies"] == []
    bridge.handle({"type": "takumion-link", "id": 9, "method": "nope"})
    assert posted[-1]["error"]["code"] == "bad-request"


def test_bridge_send_runs_off_the_calling_thread(world):
    posted = []
    caller = threading.current_thread()
    seen = []
    original = world.service.send
    world.service.send = lambda req: (seen.append(threading.current_thread()), original(req))[1]
    params = {"doc": {"key": "prj_1", "name": "B"}, "mode": "add", "format": "stl",
              "parts": [{"bodyKey": "body_1", "name": "Base", "data": base64.b64encode(b"x").decode()}]}
    Bridge(world.service, posted.append).handle({"type": "takumion-link", "id": 1, "method": "send", "params": params})
    assert wait_until(lambda: posted)
    assert seen[0] is not caller
    assert posted[0]["result"]["results"][0]["action"] == "added"


def make_tab(world, tmp_path):
    posted, opened = [], []
    pairing = PairingBook(tmp_path / "pairings.json")
    server = LinkServer(world.service, pairing, free_ports(1), log=lambda line: None)
    tab = TabController(service=world.service, pairing=pairing, server=server,
                        launcher=Launcher(opener=opened.append), post=posted.append, log=lambda line: None)
    return tab, pairing, server, posted, opened


def test_tab_pairing_flow(world, tmp_path):
    tab, pairing, _, posted, _ = make_tab(world, tmp_path)
    tab.handle({"type": "tab", "action": "ready"})
    assert posted[-1]["type"] == "tab-state" and posted[-1]["state"]["code"] is None
    tab.handle({"type": "tab", "action": "pair"})
    code = posted[-1]["state"]["code"]
    assert len(code["value"]) == 6 and 0 < code["secondsLeft"] <= 120
    pairing.pair(code["value"], "https://app.takumion.com", "Chrome")
    state = posted[-1]["state"]
    assert state["code"] is None and state["pairings"][0]["clientName"] == "Chrome"
    tab.handle({"type": "tab", "action": "revoke", "id": state["pairings"][0]["id"]})
    assert posted[-1]["state"]["pairings"] == []


def test_tab_state_shows_server_and_setup(world, tmp_path):
    tab, _, server, posted, opened = make_tab(world, tmp_path)
    server.start()
    try:
        state = tab.state()
        assert state["server"] == {"port": server.port, "error": None}
        assert state["setup"] == {"loadGeometryOnly": "unknown", "showCard": False}, "no card before a 3MF fails"
        world.orca.behaviour = "ignore"
        world.service.send(parse_send({"doc": {"key": "p", "name": "P"}, "mode": "add", "format": "3mf",
                                       "parts": [{"bodyKey": "b", "name": "B", "data": "AA=="}]}))
        assert tab.state()["setup"]["showCard"] is True
        tab.handle({"type": "tab", "action": "dismiss-setup"})
        assert posted[-1]["state"]["setup"]["showCard"] is False
        tab.handle({"type": "tab", "action": "open-takumion", "target": "desktop"})
        assert opened == [DESKTOP_URL] and DESKTOP_URL in posted[-1]["state"]["notice"]
        tab.handle({"type": "tab", "action": "open-takumion", "target": "nowhere"})
        assert "Could not open" in posted[-1]["state"]["notice"]
    finally:
        server.stop()


def test_hosted_url_and_bad_config():
    from link.launcher import WEB_URL, checked_web_url, hosted_url

    assert hosted_url("http://localhost:5180/") == "http://localhost:5180/?host=orca"
    assert hosted_url("https://app.takumion.com/?project=p&host=x") == "https://app.takumion.com/?project=p&host=orca"
    assert checked_web_url("javascript:alert(1)") == WEB_URL
    assert checked_web_url(None) == WEB_URL
    assert Launcher("http://localhost:5180/").here_url == "http://localhost:5180/?host=orca"


def test_bridge_hello_names_the_tab_page(world):
    posted = []
    Bridge(world.service, posted.append, lambda: "http://127.0.0.1:47390/tab").handle(
        {"type": "takumion-link", "id": 1, "method": "hello"})
    assert posted[-1]["result"]["tab"] == {"url": "http://127.0.0.1:47390/tab"}
    Bridge(world.service, posted.append).handle({"type": "takumion-link", "id": 2, "method": "hello"})
    assert "tab" not in posted[-1]["result"]
