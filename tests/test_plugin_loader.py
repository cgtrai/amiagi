"""Tests for PluginLoader (Phase 10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from amiagi.application.plugin_loader import PluginInfo, PluginLoader


@pytest.fixture()
def plugins_dir(tmp_path: Path) -> Path:
    d = tmp_path / "plugins"
    d.mkdir()
    return d


class TestPluginInfo:
    def test_to_dict(self) -> None:
        p = PluginInfo(name="test", version="1.0")
        d = p.to_dict()
        assert d["name"] == "test"
        assert d["version"] == "1.0"

    def test_defaults(self) -> None:
        p = PluginInfo(name="x")
        assert p.loaded is False
        assert p.error == ""


class TestPluginLoader:
    def test_empty_dir(self, plugins_dir: Path) -> None:
        loader = PluginLoader(plugins_dir=plugins_dir)
        discovered = loader.discover_directory()
        assert discovered == []

    def test_discover_py_files(self, plugins_dir: Path) -> None:
        (plugins_dir / "my_plugin.py").write_text("__version__ = '0.1'\ndef register(): pass\n")
        (plugins_dir / "_private.py").write_text("# hidden\n")
        loader = PluginLoader(plugins_dir=plugins_dir)
        discovered = loader.discover_directory()
        assert len(discovered) == 1
        assert discovered[0].name == "my_plugin"

    def test_load_directory_plugin(self, plugins_dir: Path) -> None:
        (plugins_dir / "hello.py").write_text(
            '"""Hello plugin."""\n__version__ = "1.2"\ndef register(): return "ok"\n'
        )
        loader = PluginLoader(plugins_dir=plugins_dir)
        loader.discover_directory()
        info = loader.load("hello")
        assert info.loaded is True
        assert info.version == "1.2"
        assert loader.is_loaded("hello")
        assert loader.get_callable("hello") is not None

    def test_load_nonexistent(self, plugins_dir: Path) -> None:
        loader = PluginLoader(plugins_dir=plugins_dir)
        info = loader.load("nonexistent")
        assert info.loaded is False
        assert "not found" in info.error.lower()

    def test_list_plugins(self, plugins_dir: Path) -> None:
        (plugins_dir / "a.py").write_text("def register(): pass\n")
        (plugins_dir / "b.py").write_text("def register(): pass\n")
        loader = PluginLoader(plugins_dir=plugins_dir)
        loader.discover_directory()
        assert len(loader.list_plugins()) == 2

    def test_unload(self, plugins_dir: Path) -> None:
        (plugins_dir / "rem.py").write_text("def register(): pass\n")
        loader = PluginLoader(plugins_dir=plugins_dir)
        loader.discover_directory()
        loader.load("rem")
        assert loader.unload("rem") is True
        assert loader.get("rem") is None
        assert loader.unload("rem") is False

    def test_load_all(self, plugins_dir: Path) -> None:
        (plugins_dir / "c.py").write_text("def register(): pass\n")
        loader = PluginLoader(plugins_dir=plugins_dir)
        results = loader.load_all()
        assert len(results) >= 1
        assert all(r.loaded for r in results)

    def test_no_plugins_dir(self) -> None:
        loader = PluginLoader(plugins_dir=None)
        assert loader.discover_directory() == []

    def test_discover_entry_points(self) -> None:
        loader = PluginLoader()
        # Should not raise even if no amiagi.plugins entry points exist
        discovered = loader.discover_entry_points()
        assert isinstance(discovered, list)

    def test_to_dict(self, plugins_dir: Path) -> None:
        loader = PluginLoader(plugins_dir=plugins_dir)
        d = loader.to_dict()
        assert "plugins_dir" in d
        assert isinstance(d["plugins"], list)
