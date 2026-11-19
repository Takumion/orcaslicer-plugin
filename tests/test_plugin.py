"""The entry file and the release bundle, loaded the way Orca loads a plugin:
as a module from its installed path, with our fake ``orca`` importable."""

import functools
import importlib
import importlib.util
import json
import re
import sys
import urllib.request
import zipfile
from pathlib import Path

import pytest

from conftest import free_ports
from fake_orca import FakeObject, LifecycleEvent, make_fake_orca
import link
from link.protocol import PORTS

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "packaging"))
import bundle  # noqa: E402
import build_wheel  # noqa: E402


def install(source: Path, data_dir: Path) -> Path:
    """Copy a plugin file to ``{data_dir}/orca_plugins/<file>/<file>`` like Orca does."""
    target = data_dir / "orca_plugins" / source.name / source.name
    target.parent.mkdir(parents=True)
    target.write_bytes(source.read_bytes())
    return target


def load(path: Path, monkeypatch, orca):
    monkeypatch.setitem(sys.modules, "orca", orca)
    spec = importlib.util.spec_from_file_location(f"plugin_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_wheel(whl: Path, data_dir: Path, monkeypatch, orca):
    """Install and import a wheel the way Orca does: extracted to
    ``orca_plugins/<file>/__whl_extracted__/<package>/``, that folder on
    ``sys.path``, then the package ``Import-Name`` names."""
    extracted = install(whl, data_dir).parent / "__whl_extracted__" / "takumion_link"
    with zipfile.ZipFile(whl) as archive:
        meta = next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))
        assert "Import-Name: takumion_link" in archive.read(meta).decode()
        archive.extractall(extracted)
    monkeypatch.setitem(sys.modules, "orca", orca)
    monkeypatch.syspath_prepend(str(extracted))
    for name in [n for n in sys.modules if n == "takumion_link" or n.startswith("takumion_link.")]:
        monkeypatch.delitem(sys.modules, name)
    return importlib.import_module("takumion_link")


@pytest.fixture(params=["source", "bundle", "wheel"])
def plugin(request, tmp_path, monkeypatch):
    """The plugin loaded from source (repo on sys.path), the bundle, or the wheel."""
    data_dir = tmp_path / "OrcaSlicer"
    orca = make_fake_orca()
    if request.param == "wheel":
        module = load_wheel(build_wheel.build(REPO, tmp_path / "dist"), data_dir, monkeypatch, orca)
    else:
        source = REPO / "takumion_link.py" if request.param == "source" else bundle.build(REPO, tmp_path / "dist" / "takumion_link_any.py")
        module = load(install(source, data_dir), monkeypatch, orca)
    yield module, orca, data_dir
    for cls in orca.calls["capabilities"]:
        if getattr(cls, "_test_instance", None) and cls._test_instance.app:
            cls._test_instance.on_unload()


def start_tab(orca, monkeypatch, data_dir):
    (package,) = orca.calls["packages"]
    package().register_capabilities()
    (tab_cls,) = orca.calls["capabilities"]
    tab = tab_cls()
    tab_cls._test_instance = tab
    return tab


def test_permissions_and_registration(plugin, monkeypatch):
    module, orca, data_dir = plugin
    start_tab(orca, monkeypatch, data_dir)
    (asked,) = orca.calls["permissions"]
    assert asked["threading"] is True
    assert asked["network_socket"] == [f"('127.0.0.1', {port})" for port in PORTS], \
        "no socket path at load: Orca re-asks about paths on every start"


def test_load_serve_refresh_unload(plugin, monkeypatch):
    module, orca, data_dir = plugin
    monkeypatch.setattr(module, "LinkApp", functools.partial(module.LinkApp, ports=free_ports(1)))
    tab = start_tab(orca, monkeypatch, data_dir)
    tab.on_load()
    app = tab.app
    assert app is not None and app.server.port is not None
    with urllib.request.urlopen(f"http://127.0.0.1:{app.server.port}/v1/hello", timeout=5) as reply:
        assert json.load(reply)["app"] == "takumion-orca-link"

    html = tab.get_ui()
    assert "<script>" in html and "Takumion Link" in html and 'src="app.js"' not in html
    assert app.service.orca_version == "2.5.1"
    nonce = re.search(r'name="takumion-tab" content="([^"]+)"', html).group(1)
    assert nonce == app.tab.nonce
    assert '<meta name="takumion-open"' in html, "the tab's first page goes on to Takumion by default"
    with urllib.request.urlopen(f"http://127.0.0.1:{app.server.port}/tab", timeout=5) as reply:
        assert '<meta name="takumion-open"' not in reply.read().decode(), "/tab is the way back: it never jumps away"
    orca.config = json.dumps({"openTakumionInTab": "false"})
    assert '<meta name="takumion-open"' not in tab.get_ui(), "the Config switch keeps the plugin's page"
    orca.config = "{}"
    tab.on_message(json.dumps({"type": "tab", "nonce": nonce, "action": "ready"}))

    linked = str(data_dir / "takumion" / "links" / "p" / "b" / "Base.stl")
    orca.plater_obj.the_model.items.append(FakeObject(1, "Base", linked))
    tab.on_lifecycle_event(LifecycleEvent.ObjectAdded, None)
    assert app.plate.on_plate(linked)

    assert tab.posted[-1]["state"]["takumionHere"].startswith("https://app.takumion.com/?host=orca#orcaLinkKey=")

    tab.on_message(json.dumps({"type": "tab", "action": "pair"}))
    assert app.pairing.live_code() is None, "a message without the nonce is ignored"
    tab.on_message(json.dumps({"type": "tab", "nonce": nonce, "action": "pair"}))
    assert tab.posted[-1]["type"] == "tab-state" and tab.posted[-1]["state"]["code"]

    tab.on_unload()
    assert tab.app is None and app.server.port is None


def test_versions_agree():
    manifest = (REPO / "takumion_link.py").read_text()
    assert re.search(r'^# version = "([^"]+)"', manifest, re.M).group(1) == link.__version__
    pyproject = (REPO / "pyproject.toml").read_text()
    assert f'version = "{link.__version__}"' in pyproject


def test_bundle_is_small_and_starts_with_the_manifest(tmp_path):
    out = bundle.build(REPO, tmp_path / "b.py")
    text = out.read_text()
    assert out.stat().st_size < bundle.MAX_BUNDLE_BYTES
    assert text.startswith("# /// script\n") and "[tool.orcaslicer.plugin]" in text.split("# ///\n")[0] + "# ///"
    assert "from link" not in text.split("# ---------------------------------------------------------------------------")[-1]


def test_protocol_doc_is_present():
    doc = (REPO / "docs" / "slicer-link-protocol.md").read_text()
    assert doc.startswith("# Slicer link protocol — v1")


def test_the_page_renders_from_where_it_is_installed(plugin):
    """Source, bundle and wheel each find the frontend: the wheel keeps it two levels above ``page``."""
    module, _orca, _data_dir = plugin
    html = module.render_page()
    assert "<html" in html.lower() and "<script" in html.lower()
