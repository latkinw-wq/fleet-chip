"""fleet_data — read-only snapshot of kanban workers and cron jobs.

Every source is opened read-only (``file:<path>?mode=ro``): the plugin never
writes to disk, so the dispatcher's writer is never blocked by a read here.
A missing, empty or locked database is a skipped source with a note in
``sources_errors`` — never an exception.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import sqlite3
import time
from pathlib import Path

# Thresholds; plugin.yaml config_schema declares the same names/defaults.
DEFAULTS = {
    "ready_minutes": 15,
    "silent_minutes": 3,
    "model_minutes": 2,
    "lost_minutes": 20,
    "done_hours": 6,
    "wait_days": 14,
    "board_rows": 100,
}

# Всё, что читается из ``plugins.entries.fleet-chip.settings``: пороги + язык.
# ``language`` не порог, поэтому в DEFAULTS его нет — иначе _thresholds пытался бы int("auto").
CONFIG_DEFAULTS = dict(DEFAULTS, language="auto")

_I18N = None


def _i18n():
    """``i18n.py`` рядом с этим файлом, загруженный по пути.

    Плагин приезжает в двух видах: пакетом (``from . import fleet_data`` → здесь можно
    было бы и relative-import) и bare-модулем из gateway, где каталога плагина нет в
    ``sys.path``. Загрузка по пути работает в обоих и не подхватит чужой ``i18n``.
    """
    global _I18N
    if _I18N is None:
        path = Path(__file__).resolve().parent / "i18n.py"
        spec = importlib.util.spec_from_file_location("fleet_chip_i18n", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _I18N = module
    return _I18N


def _t(lang, key, *args) -> str:
    return _i18n().t(lang, key, *args)


# Parked marker: an owner/orchestrator note at the start of the title.
_WAIT_RE = re.compile(r"^\s*\[wait\]\s*", re.IGNORECASE)

# Problem states first (spec order), then working, then what is parked on purpose.
ROW_ORDER = ("lost", "never_model", "api_unknown", "running_silent", "blocked_ask", "blocked_stale",
             "parked_stale", "not_started", "working", "blocked_wait", "parked")
_ORDER = {state: pos for pos, state in enumerate(ROW_ORDER)}
CHIP_KEYS = ("working", "running_silent", "never_model", "api_unknown", "not_started", "blocked_ask",
             "blocked_stale", "parked_stale", "lost", "blocked_wait", "parked")


def _home() -> Path:
    """Hermes root: kanban.db and cron/ live beside profiles/, not inside a profile home."""
    try:
        # Unwraps <root>/profiles/<name> → <root> (pure: no mkdir, no writes).
        from hermes_constants import get_default_hermes_root

        return Path(get_default_hermes_root())
    except Exception:  # noqa: BLE001 — standalone runs without the hermes package
        pass
    try:
        from hermes_cli.config import get_hermes_home

        return Path(get_hermes_home())
    except Exception:  # noqa: BLE001
        env = os.environ.get("HERMES_HOME")
        return Path(env) if env else Path.home() / ".hermes"


def _ro(path) -> sqlite3.Connection:
    con = sqlite3.connect(f"file:{Path(path).as_posix()}?mode=ro", uri=True)
    con.execute("PRAGMA busy_timeout=2000")
    con.row_factory = sqlite3.Row
    return con


def _note(errors: dict, path, exc: Exception) -> None:
    """Record a source failure once per path; the first error wins."""
    errors.setdefault(str(path), f"{type(exc).__name__}: {exc}"[:200])


def _thresholds(settings) -> dict:
    out = dict(DEFAULTS)
    for key, value in (settings or {}).items():
        if key in DEFAULTS and value is not None:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                pass
    return out


def _board_files(home: Path) -> list[Path]:
    """kanban.db plus the multi-board kanban/*.db (a board may be its own DB file)."""
    files = [home / "kanban.db"]
    extra = home / "kanban"
    if extra.is_dir():
        files.extend(sorted(p for p in extra.glob("*.db") if p.is_file()))
    return files


def _sessions(conns: dict, errors: dict, db, session_id) -> dict:
    """The worker's session row (api_call_count is the key signal) or {}."""
    if not db or not session_id:
        return {}
    if db not in conns:
        try:
            conns[db] = _ro(db)
        except Exception as exc:  # noqa: BLE001
            _note(errors, db, exc)
            conns[db] = None
    con = conns[db]
    if con is None:
        return {}
    try:
        row = con.execute(
            "SELECT api_call_count, message_count, model FROM sessions WHERE id = ?",
            (session_id,),
        ).fetchone()
    except Exception as exc:  # noqa: BLE001
        _note(errors, db, exc)
        return {}
    return dict(row) if row else {}


def _short(text, limit: int = 70) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _blocked(info: dict, lang: str):
    """blocked_ask → blocked_stale → blocked_wait, in that priority order."""
    kind = info.get("kind")
    kind = kind if isinstance(kind, str) else ""
    reason = str(info.get("reason") or "")
    parents = info.get("parents")
    free_reason = bool(reason) and reason != "initial_status"
    if (kind in ("needs_input", "capability") or free_reason or info.get("after_claim")
            or int(info.get("recurrences") or 0) > 0):
        if free_reason:
            return "blocked_ask", _t(lang, "blocked_ask_reason", _short(reason))
        return "blocked_ask", _t(lang, "blocked_ask_kind", kind or _t(lang, "kind_missing"))
    if kind == "dependency":
        if parents is None:
            return "blocked_wait", _t(lang, "blocked_wait_no_parents")
        waiting = [st for st in parents if st not in ("done", "archived")]
        if waiting:
            return "blocked_wait", _t(lang, "blocked_wait_parents", len(waiting))
        return "blocked_stale", _t(lang, "blocked_stale_parents")
    if reason == "initial_status":
        return "blocked_wait", _t(lang, "blocked_initial")
    return "blocked_ask", _t(lang, "blocked_bare")


def _parked(info: dict, th: dict, lang: str):
    """parked / parked_stale: отложено маркером [wait] или нативным scheduled."""
    days = info["days"]
    if days > th["wait_days"]:
        return "parked_stale", _t(lang, "parked_stale", days, info["why"])
    return "parked", _t(lang, "parked", info["why"], days)


def _classify(status, age, hb_age, sess, last_kind, block, parked, th, lang):
    """(state, hint) for one card, or (None, "") when it does not belong in rows.

    Priority: running перекрывает всё (маркер на работающей карточке игнорируется) →
    blocked_ask/blocked_stale → parked/parked_stale → blocked_wait → not_started → working.
    """
    if status == "running":
        if last_kind == "terminal_worker_reaped":
            return "lost", _t(lang, "lost_reaped")
        if hb_age > th["lost_minutes"]:
            return "lost", _t(lang, "lost_dead", hb_age)
        if hb_age > th["silent_minutes"]:
            return "running_silent", _t(lang, "silent", hb_age)
        calls = sess.get("api_call_count")
        if calls == 0 and age > th["model_minutes"]:
            return "never_model", _t(lang, "never_model", age)
        if calls:
            return "working", _t(lang, "working_calls", calls, hb_age)
        if calls == 0:
            return "working", _t(lang, "working_start", hb_age)
        # Сессии воркера нет в state.db: главный сигнал (вызовы модели) неизмерим.
        # После первых минут «старта» это уже не «работает», а «не знаю» — и оно должно
        # быть видно, иначе зависание молча выглядит здоровьем.
        if age > th["model_minutes"]:
            return "api_unknown", _t(lang, "api_unknown", hb_age)
        return "working", _t(lang, "working_no_session", hb_age)
    if status == "blocked":
        state, hint = _blocked(block, lang)
        if state != "blocked_wait" or not parked:
            return state, hint
    if parked:
        return _parked(parked, th, lang)
    if status in ("ready", "todo") and age > th["ready_minutes"]:
        return "not_started", _t(lang, "not_started", age)
    return None, ""


def _read_board(path: Path, now: float, th: dict, errors: dict, conns: dict, lang: str):
    """(rows, done_in_window) for one board DB."""
    name = "default" if path.stem == "kanban" else path.stem
    try:
        con = _ro(path)
    except Exception as exc:  # noqa: BLE001
        _note(errors, path, exc)
        return [], 0
    try:
        cols = {r["name"] for r in con.execute("PRAGMA table_info(tasks)")}
        if "id" not in cols or "status" not in cols:
            raise sqlite3.DatabaseError("no tasks table")
        optional = {col: (col if col in cols else "NULL")
                    for col in ("board", "block_kind", "block_recurrences")}
        tasks = con.execute(
            "SELECT id, title, status, assignee, created_at, started_at,"
            " last_heartbeat_at, current_run_id, completed_at,"
            " {board} AS board, {block_kind} AS block_kind,"
            " {block_recurrences} AS block_recurrences FROM tasks".format(**optional)
        ).fetchall()
        admitted: dict = {}
        # Привязка карточки к сессии воркера: берём ЛЮБОЕ событие, в payload которого есть
        # session_id (сегодня это `owner_admitted`). Привязка к имени события была бы неявным
        # контрактом: переименуют kind — живые карточки тихо потеряют связь с сессией и
        # зависший воркер будет выглядеть работающим.
        for row in con.execute(
            "SELECT task_id, payload FROM task_events"
            " WHERE payload LIKE '%session_id%' ORDER BY id DESC"
        ):
            admitted.setdefault(row["task_id"], row["payload"])
        last_event = {
            r["task_id"]: r["kind"]
            for r in con.execute(
                "SELECT task_id, kind FROM task_events"
                " WHERE id IN (SELECT MAX(id) FROM task_events GROUP BY task_id)")
        }
        # Parked age is measured from the card's newest event (there is no updated_at).
        last_event_at = dict(con.execute(
            "SELECT task_id, MAX(created_at) FROM task_events GROUP BY task_id"))
        # The blocking reason lives only in the event payload (tasks.block_kind is
        # almost always NULL); the last 'blocked' event is the current block.
        last_block: dict = {}
        for row in con.execute(
            "SELECT id, task_id, payload FROM task_events WHERE kind = 'blocked'"
            " ORDER BY id DESC"
        ):
            last_block.setdefault(row["task_id"], (row["id"], row["payload"]))
        last_claim = dict(con.execute(
            "SELECT task_id, MAX(id) FROM task_events WHERE kind = 'claimed' GROUP BY task_id"))
        try:
            parents: dict = {}
            for row in con.execute("SELECT l.child_id, t.status FROM task_links l"
                                   " LEFT JOIN tasks t ON t.id = l.parent_id"):
                parents.setdefault(row["child_id"], []).append(row["status"] or "")
        except Exception:  # noqa: BLE001 — no task_links: dependency detail degrades
            parents = None
    except Exception as exc:  # noqa: BLE001
        _note(errors, path, exc)
        return [], 0
    finally:
        con.close()

    rows, done = [], 0
    for t in tasks:
        status = t["status"] or ""
        raw_title = t["title"] or ""
        title = _WAIT_RE.sub("", raw_title)
        created = float(t["created_at"] or 0)
        # Ages are measured from created_at: the `promoted` event time is not read
        # (shortcut: a card promoted long after creation looks younger than it is).
        age = max((now - created) / 60.0, 0.0)
        hb = t["last_heartbeat_at"] or t["started_at"] or created or now
        hb_age = max((now - float(hb)) / 60.0, 0.0)
        if status in ("done", "archived"):
            if t["completed_at"] and now - float(t["completed_at"]) <= th["done_hours"] * 3600:
                done += 1
            continue
        try:
            meta = json.loads(admitted.get(t["id"]) or "{}")
        except Exception:  # noqa: BLE001 — a malformed payload only loses the session link
            meta = {}
        sess = _sessions(conns, errors, meta.get("db"), meta.get("session_id"))
        block: dict = {}
        if status == "blocked":
            event_id, event_payload = last_block.get(t["id"], (None, None))
            try:
                block = json.loads(event_payload or "{}")
            except Exception:  # noqa: BLE001 — garbage payload: classify without it
                block = {}
            block = {
                "kind": t["block_kind"] or block.get("kind"),
                "reason": block.get("reason"),
                "recurrences": t["block_recurrences"] or block.get("recurrences"),
                "after_claim": bool(event_id and last_claim.get(t["id"])
                                    and event_id > last_claim[t["id"]]),
                "parents": None if parents is None else parents.get(t["id"], []),
            }
        marked = bool(_WAIT_RE.match(raw_title))
        parked = None
        if status == "scheduled" or (marked and status in ("ready", "todo", "blocked")):
            # running не паркуется: там решает статус, маркер игнорируется
            since = last_event_at.get(t["id"]) or created or now
            parked = {"days": max((now - float(since)) / 86400.0, 0.0),
                      "why": _t(lang, "why_wait_marker") if marked else _t(lang, "why_scheduled")}
        state, hint = _classify(status, age, hb_age, sess, last_event.get(t["id"]),
                                block, parked, th, lang)
        if not state:
            continue
        rows.append({
            "id": t["id"],
            "board": t["board"] or name,
            "title": title,
            "assignee": t["assignee"] or "",
            "state": state,
            "age_min": round(age, 1),
            "heartbeat_age_min": round(hb_age, 1),
            "api_calls": sess.get("api_call_count"),
            "messages": sess.get("message_count"),
            "model": sess.get("model"),
            "run_id": t["current_run_id"],
            "hint": hint,
        })
    return rows, done


def _cron(home: Path, now: float, errors: dict) -> dict:
    jobs: list = []
    jobs_path = home / "cron" / "jobs.json"
    try:
        data = json.loads(jobs_path.read_text(encoding="utf-8"))
        jobs = data.get("jobs") if isinstance(data, dict) else data
        jobs = jobs if isinstance(jobs, list) else []
    except Exception as exc:  # noqa: BLE001
        _note(errors, jobs_path, exc)
    failing = []
    last_run: dict = {}
    failed_24h = 0
    db = home / "cron" / "executions.db"
    try:
        con = _ro(db)
        try:
            # Последний прогон по каждой задаче: (status, delivery_outcome).
            # ORDER BY rowid — не started_at: в части схем этой таблицы такой колонки нет
            # (у фикстур и у старых баз), а порядок вставки и есть хронология.
            for row in con.execute(
                    "SELECT job_id, status, delivery_outcome FROM executions ORDER BY rowid"):
                last_run[str(row["job_id"])] = (row["status"], row["delivery_outcome"])
            # finished_at is ISO text with the host's offset, so a lexical compare
            # against a same-format cutoff is exact (shortcut: breaks only if the
            # host timezone changes mid-window).
            cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 86400))
            failed_24h = con.execute(
                "SELECT COUNT(*) FROM executions WHERE delivery_outcome = 'failed'"
                " AND COALESCE(finished_at, '') >= ?", (cutoff,)).fetchone()[0]
        finally:
            con.close()
    except Exception as exc:  # noqa: BLE001
        _note(errors, db, exc)
        cutoff = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now - 86400))

    # «Падает» — значит «упал И отчёт до меня не дошёл». Мониторы у нас по контракту
    # выходят с кодом 1, когда НАШЛИ проблемы: такой прогон помечен failed, но его
    # stdout доставлен владельцу (delivery_outcome=delivered/suppressed_acked) — это
    # работа, а не поломка. Поэтому судим по последнему прогону из executions.db,
    # а last_status из jobs.json оставляем только как признак «задача вообще падала».
    REPORTED = ("delivered", "suppressed_acked")
    for job in jobs:
        if not job.get("enabled", True):
            continue
        jid = str(job.get("id"))
        last = last_run.get(jid)
        if last is not None:
            status, delivery = last
            if status == "failed" and delivery not in REPORTED:
                failing.append(jid)
            elif delivery == "failed":
                failing.append(jid)
            continue
        # Прогонов в executions.db нет (новая задача) — падаем на last_status из jobs.json.
        if str(job.get("last_status") or "") in ("error", "delivery_failed"):
            failing.append(jid)
    return {"jobs": len(jobs), "failing": failing, "delivery_failed_24h": failed_24h}


def board(settings=None) -> dict:
    """The snapshot shared by the tool, ``/fleet`` and the ``/board`` route."""
    th = _thresholds(settings)
    # Язык один на всё: подсказки строк, текстовый отчёт и подписи чипа (JS берёт ``lang``).
    lang = _i18n().resolve((settings or {}).get("language"))
    home = _home()
    now = time.time()
    errors: dict = {}
    conns: dict = {}
    rows: list = []
    done = 0
    for path in _board_files(home):
        got, done_n = _read_board(path, now, th, errors, conns, lang)
        rows.extend(got)
        done += done_n
    for con in conns.values():
        if con is not None:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass
    rows.sort(key=lambda row: (_ORDER[row["state"]], -row["age_min"]))
    chip = {state: 0 for state in CHIP_KEYS}
    for row in rows:
        chip[row["state"]] += 1
    chip["done_6h"] = done
    return {
        "generated_at": now,
        "lang": lang,
        "chip": chip,
        "rows": rows[: th["board_rows"]],
        "cron": _cron(home, now, errors),
        "sources_errors": [{"path": path, "error": text} for path, text in errors.items()],
    }


_MARKS = {"lost": "💥", "running_silent": "⏱", "never_model": "🧩", "not_started": "⏸",
          "blocked_ask": "⛔", "blocked_stale": "🧷", "blocked_wait": "💤",
          "parked": "🕗", "parked_stale": "🗓", "working": "🔄"}


def render_text(snapshot: dict) -> str:
    """The ``/fleet`` answer: one summary line, one block per card, cron footer.

    Язык берётся из самого снимка: ``/fleet`` печатает то же, что видно в чипе.
    """
    lang = snapshot.get("lang") or _i18n().FALLBACK
    chip = snapshot.get("chip") or {}
    lines = [_t(lang, "render_header").format(**chip)]
    for row in snapshot.get("rows") or []:
        lines.append(_t(lang, "render_row", _MARKS.get(row["state"], "•"), row["id"],
                        row["assignee"] or _t(lang, "no_assignee"), (row["title"] or "")[:80]))
        lines.append("   %s" % row["hint"])
    if not snapshot.get("rows"):
        lines.append(_t(lang, "render_empty"))
    cron = snapshot.get("cron") or {}
    tail = _t(lang, "render_cron", cron.get("jobs", 0))
    if cron.get("failing"):
        tail += _t(lang, "render_failing", ", ".join(cron["failing"]))
    if cron.get("delivery_failed_24h"):
        tail += _t(lang, "render_delivery", cron["delivery_failed_24h"])
    lines.append(tail)
    for err in snapshot.get("sources_errors") or []:
        lines.append(_t(lang, "render_error", Path(err["path"]).name, err["error"]))
    return "\n".join(lines)
