"""The loopback HTTP transport, over real sockets."""

import base64
import http.client
import json
import socket

import pytest

from conftest import free_ports
from link.pairing import PairingBook
from link.server import LinkServer

ORIGIN = "https://app.takumion.com"


@pytest.fixture
def served(world, tmp_path):
    pairing = PairingBook(tmp_path / "pairings.json")
    server = LinkServer(world.service, pairing, free_ports(2), log=lambda line: None)
    server.start()
    yield server, pairing
    server.stop()


def request(server, method, path, body=None, headers=None, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    data = json.dumps(body).encode() if body is not None else None
    all_headers = {"Host": host or f"127.0.0.1:{server.port}"}
    if data is not None:
        all_headers["Content-Type"] = "application/json"
    all_headers.update(headers or {})
    conn.request(method, path, body=data, headers=all_headers)
    response = conn.getresponse()
    raw = response.read()
    conn.close()
    return response.status, dict(response.getheaders()), json.loads(raw) if raw else None


def pair(server, pairing, origin=ORIGIN):
    code = pairing.start_code()
    headers = {"Origin": origin} if origin else {}
    status, _, body = request(server, "POST", "/v1/pair", {"code": code, "clientName": "Takumion (Chrome)"}, headers)
    assert status == 200
    return body["token"]


def auth(token, origin=ORIGIN):
    headers = {"Authorization": f"Bearer {token}"}
    if origin:
        headers["Origin"] = origin
    return headers


def send_body(mode="update"):
    return {"doc": {"key": "prj_1", "name": "Bracket"}, "mode": mode, "format": "stl",
            "parts": [{"bodyKey": "body_1", "name": "Base", "data": base64.b64encode(b"solid x").decode()}]}


def test_hello_answers_anyone(served):
    server, _ = served
    status, headers, body = request(server, "GET", "/v1/hello", headers={"Origin": "https://anything.example"})
    assert status == 200
    assert body["app"] == "takumion-orca-link" and body["protocol"] == 1 and body["paired"] is False
    assert body["setup"] == {"loadGeometryOnly": "unknown"}
    assert headers["Access-Control-Allow-Origin"] == "https://anything.example"


def test_foreign_host_is_refused(served):
    server, _ = served
    for host in ("evil.example", f"evil.example:{server.port}", "127.0.0.1:1"):
        status, _, body = request(server, "GET", "/v1/hello", host=host)
        assert status == 421 and body["error"]["code"] == "bad-host"
    assert request(server, "GET", "/v1/hello", host=f"localhost:{server.port}")[0] == 200


def test_pairing_and_origin_binding(served):
    server, pairing = served
    status, _, body = request(server, "POST", "/v1/pair", {"code": "nope"}, {"Origin": ORIGIN})
    assert status == 403 and body["error"]["code"] == "forbidden"
    token = pair(server, pairing)
    assert request(server, "GET", "/v1/hello", headers=auth(token))[2]["paired"] is True
    assert request(server, "GET", "/v1/hello", headers=auth(token, "https://evil.example"))[2]["paired"] is False
    status, headers, body = request(server, "GET", "/v1/links?doc=prj_1", headers=auth(token, "https://evil.example"))
    assert status == 401 and body["error"]["code"] == "unauthorized"
    assert headers["Access-Control-Allow-Origin"] == "https://evil.example", "errors are readable by any origin"


def test_success_replies_only_reach_paired_origins(served):
    server, pairing = served
    token = pair(server, pairing)
    status, headers, _ = request(server, "GET", "/v1/links?doc=prj_1", headers=auth(token))
    assert status == 200 and headers["Access-Control-Allow-Origin"] == ORIGIN
    pairing.revoke_token(token)
    status, headers, body = request(server, "GET", "/v1/links?doc=prj_1", headers=auth(token))
    assert status == 401 and body["error"]["code"] == "unauthorized"
    assert headers["Access-Control-Allow-Origin"] == ORIGIN, "a revoked browser can read that it must pair again"


def test_send_and_links_round_trip(served):
    server, pairing = served
    token = pair(server, pairing)
    status, headers, body = request(server, "POST", "/v1/send", send_body(), auth(token))
    assert status == 200 and body["results"][0]["action"] == "added"
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    status, _, links = request(server, "GET", "/v1/links?doc=prj_1", headers=auth(token))
    assert status == 200 and links["bodies"][0]["bodyKey"] == "body_1"


def test_native_client_without_origin(served):
    server, pairing = served
    token = pair(server, pairing, origin=None)
    assert request(server, "GET", "/v1/links?doc=prj_1", headers=auth(token, None))[0] == 200
    assert request(server, "GET", "/v1/links?doc=prj_1", headers=auth(token))[0] == 401


def test_unpair(served):
    server, pairing = served
    token = pair(server, pairing)
    status, _, body = request(server, "DELETE", "/v1/pair", headers=auth(token))
    assert status == 204 and body is None
    assert request(server, "GET", "/v1/links?doc=prj_1", headers=auth(token))[0] == 401


def test_preflight(served):
    server, pairing = served
    pna = {"Origin": ORIGIN, "Access-Control-Request-Method": "POST",
           "Access-Control-Request-Headers": "authorization, content-type",
           "Access-Control-Request-Private-Network": "true"}
    status, headers, _ = request(server, "OPTIONS", "/v1/pair", headers=pna)
    assert status == 204
    assert headers["Access-Control-Allow-Origin"] == ORIGIN
    assert headers["Access-Control-Allow-Private-Network"] == "true"
    assert "Authorization" in headers["Access-Control-Allow-Headers"]
    status, headers, _ = request(server, "OPTIONS", "/v1/send", headers=pna)
    assert headers["Access-Control-Allow-Origin"] == ORIGIN, "an unpaired origin may ask, so it can read the 401"
    assert headers["Access-Control-Allow-Private-Network"] == "true"


def test_bad_bodies(served):
    server, pairing = served
    token = pair(server, pairing)
    status, _, body = request(server, "POST", "/v1/send", {"doc": {"key": "a/b"}}, auth(token))
    assert status == 400 and body["error"]["code"] == "bad-request"
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("POST", "/v1/send", body=b"{nope", headers={**auth(token), "Host": f"127.0.0.1:{server.port}"})
    assert conn.getresponse().status == 400
    conn.close()


def test_too_large_is_refused_before_reading(served):
    server, pairing = served
    token = pair(server, pairing)
    with socket.create_connection(("127.0.0.1", server.port), timeout=10) as sock:
        head = (f"POST /v1/send HTTP/1.1\r\nHost: 127.0.0.1:{server.port}\r\nOrigin: {ORIGIN}\r\n"
                f"Authorization: Bearer {token}\r\nContent-Length: {385 * 1024 * 1024}\r\n\r\n")
        sock.sendall(head.encode())
        chunks = []
        while chunk := sock.recv(4096):
            chunks.append(chunk)
        reply = b"".join(chunks).decode()
    assert reply.startswith("HTTP/1.1 413") and '"too-large"' in reply


def test_unknown_route(served):
    server, _ = served
    assert request(server, "GET", "/v2/hello")[0] == 404


def test_next_port_when_the_first_is_taken(world, tmp_path):
    first, second = free_ports(2)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", first))
    blocker.listen()
    try:
        server = LinkServer(world.service, PairingBook(tmp_path / "p.json"), (first, second), log=lambda line: None)
        assert server.start() == second
        server.stop()
        assert server.port is None
    finally:
        blocker.close()


def test_all_ports_taken(world, tmp_path):
    (port,) = free_ports(1)
    blocker = socket.socket()
    blocker.bind(("127.0.0.1", port))
    blocker.listen()
    try:
        server = LinkServer(world.service, PairingBook(tmp_path / "p.json"), (port,), log=lambda line: None)
        with pytest.raises(OSError):
            server.start()
        assert "in use" in server.error
    finally:
        blocker.close()


def test_tab_page_is_served_without_pairing(served):
    server, _ = served
    conn = http.client.HTTPConnection("127.0.0.1", server.port, timeout=10)
    conn.request("GET", "/tab", headers={"Host": f"127.0.0.1:{server.port}"})
    reply = conn.getresponse()
    html = reply.read().decode()
    conn.close()
    assert reply.status == 200 and reply.getheader("Content-Type").startswith("text/html")
    assert "Takumion Link" in html and "<script>" in html
    assert request(server, "GET", "/tab", host="evil.example:1")[0] == 421
