# -*- coding: utf-8 -*-
"""Dynamic registration for /extensions UI modules."""
from __future__ import annotations

import importlib
from pathlib import Path
from typing import Callable, Type

from pipeline.base.base_extension import BaseExtension
from pipeline.registry.catalog import EXTENSION_SELECT_NONE


_EXTENSION_ID_ALIASES = {"document_viewer": "document_editor"}


def normalize_extension_id(value: str | None) -> str:
    """Map UI select value to active extension id ('' = none)."""
    if value is None:
        return ""
    key = str(value).strip()
    if not key or key == EXTENSION_SELECT_NONE:
        return ""
    return _EXTENSION_ID_ALIASES.get(key, key)


class ExtensionRegistry:
    def __init__(self) -> None:
        self._extensions: dict[str, Type[BaseExtension]] = {}
        self._mount_fns: dict[str, Callable] = {}

    def register(self, cls: Type[BaseExtension]) -> Type[BaseExtension]:
        inst = cls()
        self._extensions[inst.extension_id] = cls
        return cls

    def register_mount(self, extension_id: str, mount_fn: Callable) -> None:
        """Legacy hook for extensions not yet using BaseExtension.mount."""
        self._mount_fns[extension_id] = mount_fn

    def mount(self, extension_id: str, container) -> None:
        ext_id = normalize_extension_id(extension_id)
        if not ext_id:
            return
        if ext_id in self._mount_fns:
            self._mount_fns[ext_id](container)
            return
        cls = self._extensions.get(ext_id)
        if cls:
            cls().mount(container)

    def options(self) -> dict[str, str]:
        return {row["value"]: row["label"] for row in self.dropdown_options()}

    def label_for(self, ext_id: str) -> str:
        """Localized display label for any registered extension, including ones
        hidden from the dropdown (show_in_dropdown=False), e.g. profile_manager."""
        cls = self._extensions.get(ext_id)
        if cls:
            try:
                return cls().metadata().get("label") or ext_id
            except Exception:
                pass
        return self.options().get(ext_id, ext_id)

    def dropdown_options(self) -> list[dict[str, str]]:
        """Map-options list for the extension select (value, label)."""
        from services.plugins.extension_prefs import is_extension_enabled

        from pipeline.i18n import t as tr

        rows: list[dict[str, str]] = [
            {"value": EXTENSION_SELECT_NONE, "label": tr("extension.none_label")}
        ]
        for ext_id, cls in sorted(self._extensions.items()):
            meta = cls().metadata()
            if meta.get("show_in_dropdown") is False:
                continue
            if not is_extension_enabled(ext_id):
                continue
            label = meta.get("label") or meta.get("title") or ext_id
            rows.append(
                {
                    "value": ext_id,
                    "label": label,
                }
            )
        return rows

    def list_metadata(self) -> list[dict]:
        """Descriptors for Extension Library and validation."""
        out: list[dict] = []
        for ext_id, cls in sorted(self._extensions.items()):
            try:
                out.append(cls().metadata() or {"id": ext_id})
            except Exception:
                out.append({"id": ext_id})
        return out

    def discover(self, root: str = "extensions") -> None:
        self._discover_bundled(root)
        try:
            from services.plugins.paths import user_extensions_root

            user_root = Path(user_extensions_root())
            if user_root.is_dir():
                self._discover_file_modules(user_root)
        except Exception as exc:
            print(f"[LOMA] user extension discover skipped: {exc}")

    def _discover_bundled(self, root: str) -> None:
        # Resolve against the bundle/repo root, not the cwd — ensure_runtime_cwd() chdirs a
        # packaged app to its writable app-data folder (for relative data/ paths), which is
        # a different directory from where the read-only `extensions/` package actually lives.
        from services.platform_paths import resource_root

        base = Path(resource_root()) / root
        if not base.is_dir():
            return
        for pkg in sorted(base.iterdir()):
            if not pkg.is_dir() or pkg.name.startswith("_"):
                continue
            mod_path = pkg / "extension.py"
            if not mod_path.exists():
                continue
            mod_name = f"{root}.{pkg.name}.extension"
            try:
                mod = importlib.import_module(mod_name)
            except Exception as exc:
                print(f"[LOMA] extension load failed {mod_name}: {exc}")
                # print() is invisible in a packaged .exe (console=False redirects
                # stdout to devnull) — an extension silently failing to import (e.g. a
                # missing native dependency for a specific machine) previously left no
                # trace anywhere the user could find, just "the extension isn't there".
                # This lands in the same file Settings -> About -> "Open log file" opens.
                try:
                    from services.app_log import log_exception

                    log_exception(f"[extensions] {mod_name} failed to load")
                except Exception:
                    pass
                continue
            self._register_from_module(mod)

    def _discover_file_modules(self, base: Path) -> None:
        import importlib.util

        for pkg in sorted(base.iterdir()):
            if not pkg.is_dir() or pkg.name.startswith("_"):
                continue
            mod_path = pkg / "extension.py"
            if not mod_path.exists():
                continue
            spec = importlib.util.spec_from_file_location(f"loma_user_ext_{pkg.name}", mod_path)
            if not spec or not spec.loader:
                continue
            mod = importlib.util.module_from_spec(spec)
            try:
                spec.loader.exec_module(mod)
            except Exception as exc:
                print(f"[LOMA] user extension load failed {mod_path}: {exc}")
                continue
            self._register_from_module(mod)

    def _register_from_module(self, mod) -> None:
        for attr in dir(mod):
            obj = getattr(mod, attr)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseExtension)
                and obj is not BaseExtension
            ):
                self.register(obj)


extension_registry = ExtensionRegistry()
