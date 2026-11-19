"""fleet-chip — read-only приборка наших процессов: канбан-воркеры и кроны.

Tool ``fleet_status`` returns the JSON snapshot; ``/fleet`` renders the same
snapshot as a short Russian text table for chat and Telegram. Nothing is
written anywhere: see ``fleet_data``.
"""

from __future__ import annotations

SCHEMA = {
    "name": "fleet_status",
    "description": (
        "Snapshot of kanban worker and cron health: who is working, who is silent, "
        "who never reached the model (running + fresh heartbeat but api_call_count=0), "
        "who is lost or blocked, and cron failures. Read-only."
    ),
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
}


def register(ctx) -> None:
    # Imported here, not at module top: pytest collects this file bare (no parent
    # package) when the plugin directory's name is not an identifier ("fleet-chip").
    from . import fleet_data

    def settings() -> dict:
        """Thresholds and ``language`` from ``plugins.entries.fleet-chip.settings``."""
        out = {}
        for key, default in fleet_data.CONFIG_DEFAULTS.items():
            try:
                out[key] = ctx.get_config(key, default)
            except Exception:  # noqa: BLE001 — a broken config must not kill the tool
                out[key] = default
        return out

    def handle_tool(args, **kwargs):
        return fleet_data.board(settings())

    def handle_slash(raw_args: str = "", **kwargs) -> str:
        return fleet_data.render_text(fleet_data.board(settings()))

    ctx.register_tool(
        name="fleet_status",
        toolset="kanban",
        schema=SCHEMA,
        handler=handle_tool,
        emoji="🛰",
        description="Read-only kanban worker + cron health snapshot",
    )
    ctx.register_command(
        "fleet",
        handler=handle_slash,
        description="Worker and cron fleet watch: kanban cards + cron jobs",
    )
