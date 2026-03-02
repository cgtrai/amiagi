"""Phase 10 — Dynamic plugin loader (application).

Discovers and loads external tool/skill packages via Python's
``importlib.metadata.entry_points`` (group ``amiagi.plugins``).
Also supports explicit directory-based plugin loading.
"""

from __future__ import annotations

import importlib
import importlib.metadata
import importlib.util
import logging
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


logger = logging.getLogger(__name__)

ENTRY_POINT_GROUP = "amiagi.plugins"

PluginCallable = Callable[..., Any]


@dataclass
class PluginInfo:
    name: str
    version: str = ""
    description: str = ""
    module_path: str = ""
    entry_point: str = ""
    loaded: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "version": self.version,
            "description": self.description,
            "module_path": self.module_path,
            "entry_point": self.entry_point,
            "loaded": self.loaded,
            "error": self.error,
        }


class PluginLoader:
    """Discovers, loads and manages amiagi plugins."""

    def __init__(self, *, plugins_dir: Path | None = None) -> None:
        self._plugins_dir = plugins_dir
        self._registry: dict[str, PluginInfo] = {}
        self._callables: dict[str, PluginCallable] = {}
        self._lock = threading.Lock()

    # ---- discovery via entry_points ----

    def discover_entry_points(self) -> list[PluginInfo]:
        """Scan installed packages for ``amiagi.plugins`` entry points."""
        discovered: list[PluginInfo] = []
        try:
            eps = importlib.metadata.entry_points()
            # Python 3.12+ returns a SelectableGroups, earlier versions a dict
            if hasattr(eps, "select"):
                group_eps = eps.select(group=ENTRY_POINT_GROUP)
            else:
                group_eps = eps.get(ENTRY_POINT_GROUP, [])  # type: ignore[union-attr]
        except Exception:  # noqa: BLE001
            return discovered

        for ep in group_eps:
            info = PluginInfo(
                name=ep.name,
                entry_point=str(ep),
                module_path=ep.value if hasattr(ep, "value") else str(ep),
            )
            discovered.append(info)
            with self._lock:
                self._registry.setdefault(ep.name, info)
        return discovered

    # ---- discovery via directory ----

    def discover_directory(self) -> list[PluginInfo]:
        """Scan ``plugins_dir`` for ``*.py`` files and treat them as plugins."""
        if self._plugins_dir is None or not self._plugins_dir.is_dir():
            return []
        discovered: list[PluginInfo] = []
        for py_file in sorted(self._plugins_dir.glob("*.py")):
            if py_file.name.startswith("_"):
                continue
            name = py_file.stem
            info = PluginInfo(
                name=name,
                module_path=str(py_file),
            )
            discovered.append(info)
            with self._lock:
                self._registry.setdefault(name, info)
        return discovered

    # ---- loading ----

    def load(self, name: str) -> PluginInfo:
        """Load a discovered plugin by name.

        For entry-point plugins the module's ``register`` function is called
        if it exists.  For directory plugins the module is imported directly.
        """
        with self._lock:
            info = self._registry.get(name)
        if info is None:
            return PluginInfo(name=name, error="Plugin not found in registry")

        try:
            if info.entry_point:
                ep_parts = info.module_path.split(":")
                module_name = ep_parts[0]
                attr_name = ep_parts[1] if len(ep_parts) > 1 else "register"
                mod = importlib.import_module(module_name)
                callable_obj = getattr(mod, attr_name, None)
            else:
                spec = importlib.util.spec_from_file_location(name, info.module_path)
                if spec is None or spec.loader is None:
                    info.error = f"Cannot create module spec for {info.module_path}"
                    return info
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)  # type: ignore[union-attr]
                callable_obj = getattr(mod, "register", None)

            info.loaded = True
            info.description = getattr(mod, "__doc__", "") or ""
            info.version = getattr(mod, "__version__", "")

            if callable(callable_obj):
                with self._lock:
                    self._callables[name] = callable_obj

        except Exception as exc:  # noqa: BLE001
            info.loaded = False
            info.error = str(exc)

        with self._lock:
            self._registry[name] = info
        return info

    def load_all(self) -> list[PluginInfo]:
        self.discover_entry_points()
        self.discover_directory()
        results: list[PluginInfo] = []
        with self._lock:
            names = list(self._registry.keys())
        for name in names:
            results.append(self.load(name))
        return results

    # ---- query ----

    def get(self, name: str) -> PluginInfo | None:
        with self._lock:
            return self._registry.get(name)

    def list_plugins(self) -> list[PluginInfo]:
        with self._lock:
            return list(self._registry.values())

    def get_callable(self, name: str) -> PluginCallable | None:
        with self._lock:
            return self._callables.get(name)

    def is_loaded(self, name: str) -> bool:
        with self._lock:
            info = self._registry.get(name)
            return info.loaded if info else False

    def unload(self, name: str) -> bool:
        with self._lock:
            if name in self._registry:
                del self._registry[name]
                self._callables.pop(name, None)
                return True
            return False

    def to_dict(self) -> dict[str, Any]:
        with self._lock:
            return {
                "plugins_dir": str(self._plugins_dir) if self._plugins_dir else None,
                "plugins": [p.to_dict() for p in self._registry.values()],
            }


def plugin_cli_main() -> None:
    """CLI entry-point for ``amiagi-plugin`` command.

    Provides plugin management from the command line::

        amiagi-plugin list
        amiagi-plugin load <name>
        amiagi-plugin scan [--dir <path>]
    """
    import argparse
    import json as _json
    import sys

    parser = argparse.ArgumentParser(prog="amiagi-plugin", description="amiagi plugin manager")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("list", help="List discovered plugins")
    load_p = sub.add_parser("load", help="Load a specific plugin by name")
    load_p.add_argument("name", help="Plugin name")
    scan_p = sub.add_parser("scan", help="Scan directory for plugins")
    scan_p.add_argument("--dir", default="./plugins", help="Directory to scan")

    args = parser.parse_args()

    loader = PluginLoader()

    if args.command == "list":
        loader.discover_entry_points()
        loader.discover_directory()
        data = loader.to_dict()
        print(_json.dumps(data, indent=2, ensure_ascii=False))
    elif args.command == "load":
        loader.discover_entry_points()
        loader.discover_directory()
        info = loader.load(args.name)
        if info.loaded:
            print(f"Plugin '{args.name}' loaded successfully.")
        else:
            err = info.error or "not found"
            print(f"Failed to load '{args.name}': {err}", file=sys.stderr)
            sys.exit(1)
    elif args.command == "scan":
        loader = PluginLoader(plugins_dir=Path(args.dir))
        loader.discover_directory()
        loaded = loader.load_all()
        print(f"Loaded {len(loaded)} plugins from {args.dir}")
        for p in loaded:
            print(f"  - {p.name} ({p.module_path})")
    else:
        parser.print_help()
        sys.exit(1)
