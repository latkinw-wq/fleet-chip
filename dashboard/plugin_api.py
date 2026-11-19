"""fleet-chip backend — ``GET /board`` returns the same JSON as the ``fleet_status`` tool.

Mounted by the gateway at ``/api/plugins/fleet-chip/board`` when the plugin is in
``plugins.enabled``. ``fleet_data.py`` lives one directory up and is loaded by
file path: the gateway imports this file as a bare module, so the plugin dir is
not on ``sys.path`` and a plain ``import fleet_data`` would fail.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from fastapi import APIRouter

router = APIRouter()

_plugin_dir = Path(__file__).resolve().parent.parent
_module = None


def _fleet_data():
    global _module
    if _module is None:
        path = _plugin_dir / "fleet_data.py"
        spec = importlib.util.spec_from_file_location("fleet_chip_fleet_data", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _module = module
    return _module


def _settings() -> dict:
    """``plugins.entries.fleet-chip.settings`` без ``PluginContext``.

    У маршрута нет контекста плагина (``ctx.get_config`` доступен только в ``register``),
    поэтому настройки читаются из конфига напрямую. Иначе панель жила бы на дефолтах:
    изменённые владельцем пороги и язык до неё бы не доезжали.
    """
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly() or {}
        plugins = config.get("plugins") if isinstance(config, dict) else None
        entries = plugins.get("entries") if isinstance(plugins, dict) else None
        entry = entries.get("fleet-chip") if isinstance(entries, dict) else None
        settings = entry.get("settings") if isinstance(entry, dict) else None
        return dict(settings) if isinstance(settings, dict) else {}
    except Exception:  # noqa: BLE001 — битый конфиг не должен ломать приборку
        return {}


@router.get("/board")
def board() -> dict:
    """Kanban + cron snapshot (read-only)."""
    return _fleet_data().board(_settings())
