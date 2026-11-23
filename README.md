# fleet-chip

Read-only watch for kanban workers, cron jobs, background processes and delegated subagents, as a
chip in the Hermes Desktop status bar. Click the chip for the full board, right click to refresh.

The failure mode it catches: a card in `running` with a live heartbeat whose session has
`api_call_count = 0` — a worker process is up, but it never reached the model.

## What it reads

Every source is opened read-only (`file:<path>?mode=ro`): no writes, no network, no extra
processes. A missing, locked or broken database becomes a line in `sources_errors`, never an
exception.

| Source | What it gives |
|---|---|
| `kanban.db` and `kanban/*.db` | cards, their status, heartbeat, assignee |
| `profiles/*/state.db` | `api_call_count`, `message_count`, model of the worker's session |
| `kanban.db` → `task_events` | session binding, last block reason, last event per card |
| `cron/jobs.json`, `cron/executions.db` | cron jobs and the outcome of their last run |
| `<home>/processes.json` (every profile) | background processes: command, PID, start time |
| `state.db` → `async_delegations` | delegated subagents (`delegate_task`): state, goal, owner |

## Background: processes and subagents

Desktop shows background work per session in the composer status stack; the plugin collects it
across every profile at once. A background row is `kind` (`proc` or `agent`), `profile`, `state`,
`age_min`, `hint`.

Background work has no heartbeat and no model-call counter; the only honest signal is whether the
process is alive:

| State | Rule | Counted as a problem |
|---|---|---|
| `bg_running` | the process PID is alive (or the record's owner is alive with state `running`/`finalizing`) | no |
| `bg_silent` | the subagent record is in state `stalling` | yes |
| `bg_lost` | the process is gone but its record stayed; or a subagent record never completed and its owner died | yes |
| `bg_unknown` | no PID recorded or it cannot be probed — "not sure", never "working" | yes |

Liveness is checked without `subprocess` and without dependencies: `OpenProcess` +
`GetExitCodeProcess` through `ctypes` on Windows, `os.kill(pid, 0)` on POSIX. A dead process keeps
its checkpoint entry until the registry writes again, and the plugin reports it as `bg_lost` —
the registry missed the exit.

In the chip subagents and processes get their own groups with the same icons the composer status
stack draws: `codicon-agent` (the hexagon) and `codicon-server-process` (the folder with a gear).
Those are Hermes Desktop's own codicons: `@vscode/codicons` is loaded globally by the app, so the
plugin uses the same classes rather than copying SVGs. Each group keeps its own counter, so a
failure's source is visible at a glance. Finished subagents never become rows; they only count in
`done_6h`.

## States

| State | Rule | Counted as a problem |
|---|---|---|
| `lost` | `running`, heartbeat older than `lost_minutes`, or the dispatcher reaped the worker | yes |
| `never_model` | `running`, fresh heartbeat, `api_call_count = 0` for `model_minutes` | yes |
| `api_unknown` | `running`, the worker's session is not in `state.db` — model calls unmeasurable | yes |
| `running_silent` | `running`, no heartbeat for `silent_minutes` | yes |
| `blocked_ask` | blocked and asking for a decision (free-form reason, `needs_input`, block after claim) | yes |
| `blocked_stale` | blocked on dependencies whose parents are all closed — auto-promotion never fired | yes |
| `parked_stale` | parked by the `[wait]` marker or a `scheduled` status for more than `wait_days` | yes |
| `not_started` | `ready`/`todo` card older than `ready_minutes` | yes |
| `working` | `running`, fresh heartbeat, model calls going up | no |
| `blocked_wait` | waiting on live parents, or blocked from the moment it was created | no |
| `parked` | parked on purpose, still within `wait_days` | no |

`api_unknown` covers the case where the worker's session is missing from `state.db`: the main
signal cannot be measured, and calling such a card `working` would hide a hang.

Cron counts as failing when the last run failed **and** its report never arrived. Monitor jobs
that exit with code 1 after finding a problem are delivered, not failing.

## Install

```bash
git clone --branch v0.3.0 --depth 1 https://github.com/latkinw-wq/fleet-chip.git
cp -r fleet-chip ~/.hermes/plugins/fleet-chip
mkdir -p ~/.hermes/desktop-plugins/fleet-chip
cp fleet-chip/desktop/plugin.js ~/.hermes/desktop-plugins/fleet-chip/
```

Drop `--branch` to track `main` instead of the release.

Then add `fleet-chip` to `plugins.enabled` in `~/.hermes/config.yaml` and restart Hermes Desktop.
The Python half is served by the gateway, so a plugin update needs a gateway restart as well.

## Tool and command

- `fleet_status` (toolset `kanban`) returns the snapshot as JSON.
- `/fleet` prints the same snapshot as text.
- `GET /api/plugins/fleet-chip/board` serves the same JSON to the Desktop chip.

## Settings

`plugins.entries.fleet-chip.settings`:

| Key | Default | Meaning |
|---|---|---|
| `language` | `auto` | `auto`, `ru` or `en` |
| `ready_minutes` | 15 | `ready`/`todo` older than this never started |
| `silent_minutes` | 3 | `running` without a heartbeat for this long is silent |
| `model_minutes` | 2 | `running` with `api_call_count = 0` for this long never reached the model |
| `lost_minutes` | 20 | `running` without a heartbeat for this long is lost |
| `done_hours` | 6 | window of the "closed in the last N hours" counter |
| `wait_days` | 14 | parked longer than this is stale |
| `board_rows` | 100 | maximum number of rows in the snapshot |
| `bg_rows` | 20 | maximum number of background rows in the snapshot |

## Language

`language: auto` follows the system locale (`LANG`/`LC_ALL`, then the Windows UI language, then
`locale`); an unknown locale gives English. `ru` and `en` force one.

The setting covers everything the plugin draws itself: the chip and its tooltip, the panel, the
hints in the snapshot, `/fleet`. A language change applies on the next refresh — the backend reads
`/board` settings on every request, so no restart is needed.

It does not cover the settings form in the Plugins hub: Hermes renders field labels and
descriptions straight from `plugin.yaml` in a single language, so those are neutral formulas
(`running, no heartbeat N min → running_silent`) instead of prose.

## Tests

```bash
python -m pytest -q
```

The suite builds a synthetic `kanban.db`, session databases and cron tables in a temporary
directory and checks every state, the counters, sorting, the read-only guarantee and both
languages.

## License

MIT — see [LICENSE](LICENSE). Русская версия README: [README.ru.md](README.ru.md).
