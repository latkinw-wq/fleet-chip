"""Тесты снимка fleet_data на синтетической базе (все источники — временные файлы)."""

import json
import os
import sqlite3
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fleet_data  # noqa: E402

NOW = time.time()
MIN = 60.0


def _mk(path: Path, statements):
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(path))
    try:
        for sql, params in statements:
            con.execute(sql, params)
        con.commit()
    finally:
        con.close()


def _home(tmp_path: Path) -> Path:
    """Доска + сессии воркера + кроны, ровно как их читает fleet_data."""
    kanban = tmp_path / "kanban.db"
    _mk(
        kanban,
        [
            (
                "CREATE TABLE tasks (id TEXT PRIMARY KEY, title TEXT, status TEXT,"
                " assignee TEXT, created_at INTEGER, started_at INTEGER,"
                " last_heartbeat_at INTEGER, current_run_id INTEGER, completed_at INTEGER,"
                " block_kind TEXT, block_recurrences INTEGER DEFAULT 0)",
                (),
            ),
            ("CREATE TABLE task_events (id INTEGER PRIMARY KEY AUTOINCREMENT,"
             " task_id TEXT, kind TEXT, payload TEXT, created_at INTEGER)", ()),
            ("CREATE TABLE task_links (parent_id TEXT, child_id TEXT,"
             " PRIMARY KEY (parent_id, child_id))", ()),
        ],
    )
    state = tmp_path / "profiles" / "dev" / "state.db"
    _mk(
        state,
        [
            ("CREATE TABLE sessions (id TEXT PRIMARY KEY, api_call_count INTEGER,"
             " message_count INTEGER, model TEXT)", ()),
            ("INSERT INTO sessions VALUES (?,?,?,?)",
             ("local-work", 7, 21, "deepseek-flash")),
            ("INSERT INTO sessions VALUES (?,?,?,?)", ("local-never", 0, 3, "deepseek-flash")),
            ("INSERT INTO sessions VALUES (?,?,?,?)", ("local-silent", 3, 11, "deepseek-pro")),
            ("INSERT INTO sessions VALUES (?,?,?,?)", ("local-lost", 5, 30, "deepseek-pro")),
            ("INSERT INTO sessions VALUES (?,?,?,?)", ("local-reap", 2, 9, "deepseek-flash")),
        ],
    )

    tasks = [
        # id, title, status, assignee, created, started, heartbeat, run, completed, kind, rec
        ("t_work", "Работает", "running", "dev", NOW - 20 * MIN, NOW - 19 * MIN, NOW - 30, 152, None, None, 0),
        ("t_never", "Не доехал", "running", "dev", NOW - 10 * MIN, NOW - 9 * MIN, NOW - 20, 153, None, None, 0),
        ("t_silent", "Тишина", "running", "dev", NOW - 30 * MIN, NOW - 29 * MIN, NOW - 7 * MIN, 154, None, None, 0),
        ("t_lost", "Потерян", "running", "tester", NOW - 90 * MIN, NOW - 89 * MIN, NOW - 40 * MIN, 155, None, None, 0),
        ("t_reap", "Снят диспетчером", "running", "researcher", NOW - 12 * MIN, NOW - 11 * MIN, NOW - 15, 156, None, None, 0),
        ("t_ready", "Не стартовала", "ready", "dev", NOW - 40 * MIN, None, None, None, None, None, 0),
        ("t_new", "Только что создана", "ready", "dev", NOW - 2 * MIN, None, None, None, None, None, 0),
        ("t_wait", "Отложена сразу", "blocked", "dev", NOW - 70 * 60 * MIN, None, None, None, None, None, 0),
        ("t_ask", "Просит решения", "blocked", "dev", NOW - 40 * MIN, None, None, None, None, None, 0),
        ("t_claim", "Заблокировал сам воркер", "blocked", "dev", NOW - 30 * MIN, None, None, None, None, None, 0),
        ("t_bare", "Блок без объяснения", "blocked", "dev", NOW - 50 * MIN, None, None, None, None, None, 0),
        ("t_stale", "Висит без причины", "blocked", "dev", NOW - 80 * MIN, None, None, None, None, None, 0),
        ("t_park", "[wait] Снять rescue-слой", "ready", "dev", NOW - 3 * 1440 * MIN, None, None, None, None, None, 0),
        ("t_park_old", "[Wait]  Отложена давно", "ready", "dev", NOW - 40 * 1440 * MIN, None, None, None, None, None, 0),
        ("t_park_blocked", "[wait] Ждёт своего часа", "blocked", "dev", NOW - 5 * 1440 * MIN, None, None, None, None, None, 0),
        ("t_sched", "Нативная парковка", "scheduled", "dev", NOW - 3 * 1440 * MIN, None, None, None, None, None, 0),
        ("t_run_wait", "[wait] но работает", "running", "dev", NOW - 6 * MIN, NOW - 6 * MIN, NOW - 20, 157, None, None, 0),
        ("t_dwait", "Ждёт родителя", "blocked", "dev", NOW - 20 * MIN, None, None, None, None, "dependency", 0),
        ("t_done", "Только что закрыта", "done", "dev", NOW - 3 * 60 * MIN, None, None, None, NOW - 60 * MIN, None, 0),
        ("t_old_done", "Закрыта вчера", "done", "dev", NOW - 30 * 60 * MIN, None, None, None, NOW - 10 * 60 * MIN, None, 0),
        ("t_review", "На ревью", "review", "dev", NOW - 5 * MIN, None, None, None, None, None, 0),
    ]
    events = []
    session_of = {"t_work": "local-work", "t_never": "local-never", "t_silent": "local-silent",
                  "t_lost": "local-lost", "t_reap": "local-reap"}
    for tid, sid in session_of.items():
        payload = json.dumps({"db": str(state), "session_id": sid})
        events.append((tid, "owner_admitted", payload, NOW - 5 * MIN))
        events.append((tid, "heartbeat", None, NOW - 60))
    events.append(("t_reap", "terminal_worker_reaped", "{}", NOW - 120))
    # Причина блока живёт только в payload события blocked (tasks.block_kind почти всегда NULL).
    events.append(("t_wait", "blocked",
                   json.dumps({"reason": "initial_status", "status": "blocked", "actor": "default"}),
                   NOW - 70 * 60 * MIN))
    events.append(("t_ask", "claimed", None, NOW - 35 * MIN))
    events.append(("t_ask", "blocked",
                   json.dumps({"reason": "нужно решение владельца: какой парсер брать",
                               "kind": "needs_input", "recurrences": 1}), NOW - 30 * MIN))
    events.append(("t_claim", "claimed", None, NOW - 25 * MIN))
    events.append(("t_claim", "blocked",
                   json.dumps({"reason": None, "kind": None, "recurrences": 0}), NOW - 24 * MIN))
    events.append(("t_stale", "blocked",
                   json.dumps({"reason": None, "kind": "dependency"}), NOW - 75 * MIN))
    events.append(("t_dwait", "blocked",
                   json.dumps({"reason": None, "kind": "dependency"}), NOW - 15 * MIN))
    # Парковка: возраст считается от последнего события карточки (updated_at в tasks нет).
    events.append(("t_park", "commented", "{}", NOW - 2 * 1440 * MIN))
    events.append(("t_park_old", "commented", "{}", NOW - 20 * 1440 * MIN))
    events.append(("t_park_blocked", "blocked",
                   json.dumps({"reason": "initial_status", "status": "blocked"}), NOW - 1 * 1440 * MIN))
    events.append(("t_sched", "commented", "{}", NOW - 1 * 1440 * MIN))
    events.append(("t_run_wait", "heartbeat", None, NOW - 60))
    con = sqlite3.connect(str(kanban))
    try:
        con.executemany("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?)", tasks)
        con.executemany("INSERT INTO task_events (task_id, kind, payload, created_at)"
                        " VALUES (?,?,?,?)", events)
        con.executemany("INSERT INTO task_links VALUES (?,?)",
                        [("t_done", "t_stale"), ("t_work", "t_dwait")])
        con.commit()
    finally:
        con.close()

    _mk(
        tmp_path / "cron" / "executions.db",
        [
            ("CREATE TABLE executions (id TEXT, job_id TEXT, status TEXT,"
             " delivery_outcome TEXT, finished_at TEXT)", ()),
            ("INSERT INTO executions VALUES ('1','abc','completed','failed',?)",
             (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 3600)),)),
            ("INSERT INTO executions VALUES ('2','abc','completed','delivered',?)",
             (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 2 * 3600)),)),
            ("INSERT INTO executions VALUES ('3','def','completed','failed',?)",
             (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(NOW - 48 * 3600)),)),
        ],
    )
    jobs = {"jobs": [
        {"id": "abc", "enabled": True, "last_status": "ok"},
        {"id": "def", "enabled": True, "last_status": "error"},
        {"id": "ghi", "enabled": False, "last_status": "delivery_failed"},
        {"id": "jkl", "enabled": True, "last_status": "delivery_failed"},
    ], "updated_at": "2026-11-17T00:00:00+03:00"}
    (tmp_path / "cron").mkdir(parents=True, exist_ok=True)
    (tmp_path / "cron" / "jobs.json").write_text(json.dumps(jobs), encoding="utf-8")
    return tmp_path


@pytest.fixture()
def fleet_home(tmp_path, monkeypatch):
    """Синтетический HOME; язык не задан — тесты, которым он важен, передают настройку сами."""
    home = _home(tmp_path)
    monkeypatch.setattr(fleet_data, "_home", lambda: home)
    return home


@pytest.fixture()
def snapshot(fleet_home):
    # Язык фиксирован: по умолчанию он `auto` от локали машины, и на en-хосте
    # русские проверки ниже падали бы не по делу.
    return fleet_data.board({"language": "ru"})


def _states(snap):
    return {row["id"]: row["state"] for row in snap["rows"]}


def test_states(snapshot):
    states = _states(snapshot)
    assert states["t_work"] == "working"
    assert states["t_never"] == "never_model"
    assert states["t_silent"] == "running_silent"
    assert states["t_lost"] == "lost"
    assert states["t_reap"] == "lost"  # terminal_worker_reaped при живом heartbeat
    assert states["t_ready"] == "not_started"
    assert "t_new" not in states  # ready, но моложе порога
    assert "t_review" not in states
    assert "t_done" not in states and "t_old_done" not in states


def test_blocked_substates(snapshot):
    """blocked — не одно состояние: ask (внимание) / stale (внимание) / wait (отложено)."""
    states = _states(snapshot)
    rows = {row["id"]: row for row in snapshot["rows"]}
    # создана сразу заблокированной, reason = initial_status — не шумим (живой случай t_7c8763ca)
    assert states["t_wait"] == "blocked_wait"
    assert "initial_status" not in rows["t_wait"]["hint"]
    assert states["t_ask"] == "blocked_ask"  # свободный reason + kind needs_input
    assert states["t_claim"] == "blocked_ask"  # блок поставлен после claimed текущего рана
    assert states["t_bare"] == "blocked_ask"  # блок без объяснения
    assert states["t_stale"] == "blocked_stale"  # dependency, родитель закрыт
    assert states["t_dwait"] == "blocked_wait"  # dependency, родитель в работе
    assert rows["t_ask"]["hint"].startswith("просит решения")
    assert "парсер" in rows["t_ask"]["hint"]


def test_parked_and_stale(snapshot):
    """[wait]/scheduled — отложено штатно; дольше wait_days — parked_stale (проблема)."""
    states = _states(snapshot)
    rows = {row["id"]: row for row in snapshot["rows"]}
    assert states["t_park"] == "parked"  # ready + маркер [wait], 2 дня
    assert states["t_sched"] == "parked"  # нативный status=scheduled, 1 день
    assert states["t_park_blocked"] == "parked"  # initial_status + маркер → парковка важнее wait
    assert states["t_park_old"] == "parked_stale"  # 20 дней > wait_days
    assert "пора решить" in rows["t_park_old"]["hint"]
    # приставку [wait] из заголовка отрезаем (регистр не важен)
    assert rows["t_park"]["title"] == "Снять rescue-слой"
    assert rows["t_park_old"]["title"].strip() == "Отложена давно"
    # на running маркер игнорируется — решает статус; сессии воркера в фикстуре нет,
    # значит вызовы модели неизмеримы → честное «не знаю», а не «работает»
    assert states["t_run_wait"] == "api_unknown"


def test_row_fields_and_sorting(snapshot):
    rows = snapshot["rows"]
    assert [r["state"] for r in rows[:2]] == ["lost", "lost"]
    order = {row["id"]: pos for pos, row in enumerate(rows)}
    # проблемные (blocked_ask, parked_stale...) → working → отложенные (blocked_wait, parked)
    assert order["t_ask"] < order["t_park_old"] < order["t_work"]
    assert order["t_work"] < order["t_wait"] < order["t_park"]
    work = next(r for r in rows if r["id"] == "t_work")
    assert work["board"] == "default"
    assert work["api_calls"] == 7 and work["messages"] == 21
    assert work["model"] == "deepseek-flash" and work["run_id"] == 152
    assert work["age_min"] > 19 and work["heartbeat_age_min"] < 2
    assert work["hint"].startswith("работает")
    never = next(r for r in rows if r["id"] == "t_never")
    assert never["api_calls"] == 0 and "не доехал до модели" in never["hint"]


def test_chip_and_cron(snapshot):
    assert snapshot["chip"] == {
        "working": 1, "running_silent": 1, "never_model": 1, "api_unknown": 1, "not_started": 1,
        "blocked_ask": 3, "blocked_stale": 1, "parked_stale": 1, "lost": 2,
        "blocked_wait": 2, "parked": 3, "done_6h": 1,
    }
    assert snapshot["cron"] == {"jobs": 4, "failing": ["def", "jkl"], "delivery_failed_24h": 1}
    assert snapshot["sources_errors"] == []
    assert set(snapshot) == {"generated_at", "lang", "chip", "rows", "cron", "background",
                             "sources_errors"}
    # Фонов в этой фикстуре нет: слой не выдумывает строк, когда источников нет.
    assert snapshot["background"]["rows"] == []
    assert snapshot["background"]["chip"] == {"bg_running": 0, "bg_silent": 0, "bg_lost": 0,
                                              "bg_unknown": 0, "done_6h": 0}


def test_broken_db_is_a_source_error(tmp_path, monkeypatch):
    home = _home(tmp_path / "ok")
    broken = home / "kanban.db"
    broken.write_bytes(b"")  # 0 байт — sqlite не откроет как базу
    monkeypatch.setattr(fleet_data, "_home", lambda: home)
    snap = fleet_data.board()
    assert snap["rows"] == []
    paths = [err["path"] for err in snap["sources_errors"]]
    assert str(broken) in paths
    assert "DatabaseError" in next(e for e in snap["sources_errors"] if e["path"] == str(broken))["error"]


def test_missing_home_does_not_raise(tmp_path, monkeypatch):
    monkeypatch.setattr(fleet_data, "_home", lambda: tmp_path / "nope")
    snap = fleet_data.board()
    assert snap["rows"] == []
    assert {err["path"] for err in snap["sources_errors"]} == {
        str(tmp_path / "nope" / "kanban.db"),
        str(tmp_path / "nope" / "cron" / "jobs.json"),
        str(tmp_path / "nope" / "cron" / "executions.db"),
    }


def test_sources_are_read_only(tmp_path, monkeypatch):
    home = _home(tmp_path)
    monkeypatch.setattr(fleet_data, "_home", lambda: home)
    before = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in home.rglob("*") if p.is_file()}
    fleet_data.board()
    after = {p: (p.stat().st_size, p.stat().st_mtime_ns) for p in home.rglob("*") if p.is_file()}
    assert before == after


def test_render_text(snapshot):
    text = fleet_data.render_text(snapshot)
    assert text.startswith("🛰 FLEET")
    assert "t_never" in text and "не доехал до модели" in text
    assert "на паузе 3" in text and "просрочено отложенных 1" in text
    assert "[wait] Снять" not in text  # приставка отрезана из заголовка
    assert "кроны: 4" in text


def test_language_explicit_en(fleet_home):
    """Настройка ``en`` переводит и подсказки, и текст /fleet."""
    snap = fleet_data.board({"language": "en"})
    rows = {row["id"]: row for row in snap["rows"]}
    assert snap["lang"] == "en"
    assert "never reached the model" in rows["t_never"]["hint"]
    assert rows["t_ask"]["hint"].startswith("needs a decision")
    assert "time to decide" in rows["t_park_old"]["hint"]
    text = fleet_data.render_text(snap)
    assert text.startswith("🛰 FLEET · working 1")
    assert "cron: 4" in text and "failing: def, jkl" in text
    assert "не доехал" not in text


def test_language_auto_follows_locale(monkeypatch):
    """``auto``/мусор решает локаль системы; явная настройка важнее локали."""
    i18n = fleet_data._i18n()
    monkeypatch.setattr(i18n, "system_locale", lambda: "ru_RU.UTF-8")
    assert i18n.resolve(None) == "ru"
    assert i18n.resolve("auto") == "ru"
    assert i18n.resolve("EN") == "en"  # регистр не важен, и это важнее локали
    assert i18n.resolve("klingon") == "ru"  # мусор — это auto
    monkeypatch.setattr(i18n, "system_locale", lambda: "de_DE.UTF-8")
    assert i18n.resolve("auto") == "en"
    assert i18n.resolve(None) == "en"
    assert i18n.resolve("en-US") == "en"  # в настройке ждём ru/en, остальное — локаль


def test_language_locale_names():
    """Windows называет локаль словами: «Russian_Russia» — это ru, а не en."""
    i18n = fleet_data._i18n()
    assert i18n._language_of("Russian_Russia") == "ru"
    assert i18n._language_of("ru_RU.UTF-8") == "ru"
    assert i18n._language_of("ru-RU") == "ru"
    assert i18n._language_of("English_United States") == "en"
    assert i18n._language_of("") == ""


def test_language_catalog_is_complete():
    """Половина перевода хуже отсутствия перевода: набор ключей обязан совпадать."""
    catalog = fleet_data._i18n()._STRINGS
    assert set(catalog["ru"]) == set(catalog["en"])
    assert not [k for k, v in catalog["en"].items() if not str(v).strip()]
    # незнакомый язык не должен ронять отрисовку
    assert fleet_data._i18n().t("xx", "lost_reaped") == catalog["en"]["lost_reaped"]


# --------------------------------------------------------------------------- фон: процессы и субагенты

DEAD_PID = 4_000_000  # заведомо несуществующий: pid на Windows доходят до сотен тысяч


def _bg_home(tmp_path: Path) -> Path:
    """Домашний каталог с чекпоинтом процессов и записями делегирований."""
    home = tmp_path / "home"
    home.mkdir(parents=True)
    (home / "processes.json").write_text(
        json.dumps(
            [
                {"session_id": "proc_alive", "pid": os.getpid(), "pid_scope": "host",
                 "command": "python -m http.server 8080", "started_at": NOW - 7 * MIN,
                 "notify_on_complete": True},
                {"session_id": "proc_dead", "pid": DEAD_PID, "pid_scope": "host",
                 "command": "1cv8.exe ENTERPRISE /F\"D:/base\"", "started_at": NOW - 90 * MIN},
                {"session_id": "proc_nopid", "pid": None, "command": "npm run dev"},
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    state = home / "profiles" / "dev" / "state.db"
    _mk(
        state,
        [
            ("CREATE TABLE async_delegations (delegation_id TEXT PRIMARY KEY, state TEXT,"
             " dispatched_at REAL, completed_at REAL, task_json TEXT, owner_pid INTEGER)", ()),
            ("INSERT INTO async_delegations VALUES (?,?,?,?,?,?)",
             ("d_run", "running", NOW - 5 * MIN, None, json.dumps({"goal": "Собрать отчёт"}), os.getpid())),
            ("INSERT INTO async_delegations VALUES (?,?,?,?,?,?)",
             ("d_stall", "stalling", NOW - 40 * MIN, None, json.dumps({"goal": "Проверить 1С"}), os.getpid())),
            ("INSERT INTO async_delegations VALUES (?,?,?,?,?,?)",
             ("d_dead", "running", NOW - 60 * MIN, None, json.dumps({"goals": ["Сделать A", "Сделать B"]}),
              DEAD_PID)),
            ("INSERT INTO async_delegations VALUES (?,?,?,?,?,?)",
             ("d_done", "completed", NOW - 50 * MIN, NOW - 10 * MIN, "{}", os.getpid())),
            ("INSERT INTO async_delegations VALUES (?,?,?,?,?,?)",
             ("d_old", "completed", NOW - 3000 * MIN, NOW - 2000 * MIN, "{}", os.getpid())),
        ],
    )
    return home


@pytest.fixture()
def bg_snapshot(tmp_path, monkeypatch):
    home = _bg_home(tmp_path)
    monkeypatch.setattr(fleet_data, "_home", lambda: home)
    return fleet_data.board({"language": "ru"})


def _bg_states(snap):
    return {row["id"]: row for row in snap["background"]["rows"]}


def test_background_processes(bg_snapshot):
    rows = _bg_states(bg_snapshot)
    assert rows["proc_alive"]["kind"] == "proc"
    assert rows["proc_alive"]["state"] == "bg_running"
    assert rows["proc_alive"]["profile"] == "default"
    assert rows["proc_alive"]["notify"] is True
    assert "жив" in rows["proc_alive"]["hint"]
    # Запись осталась, процесса нет — тот же класс, что `lost` у карточки.
    assert rows["proc_dead"]["state"] == "bg_lost"
    assert str(DEAD_PID) in rows["proc_dead"]["hint"]
    # PID не записан — честное «не проверить», а не выдуманное «работает».
    assert rows["proc_nopid"]["state"] == "bg_unknown"


def test_background_agents(bg_snapshot):
    rows = _bg_states(bg_snapshot)
    assert rows["d_run"]["state"] == "bg_running"
    assert rows["d_run"]["title"] == "Собрать отчёт"
    assert rows["d_run"]["profile"] == "dev"
    assert rows["d_stall"]["state"] == "bg_silent"
    assert "залип" in rows["d_stall"]["hint"]
    # Владелец делегирования мёртв: запись живёт, завершения не будет.
    assert rows["d_dead"]["state"] == "bg_lost"
    assert rows["d_dead"]["title"].startswith("Сделать A") and "(+1)" in rows["d_dead"]["title"]
    # Завершённые — не строки: свежие идут в счётчик, старые не видит никто.
    assert "d_done" not in rows and "d_old" not in rows


def test_background_chip_and_order(bg_snapshot):
    chip = bg_snapshot["background"]["chip"]
    assert chip == {"bg_running": 2, "bg_silent": 1, "bg_lost": 2, "bg_unknown": 1, "done_6h": 1}
    states = [row["state"] for row in bg_snapshot["background"]["rows"]]
    # Проблемные первыми, работающие — последними.
    assert states == sorted(states, key=fleet_data.BG_ORDER.index)


def test_background_in_fleet_text(bg_snapshot):
    text = fleet_data.render_text(bg_snapshot)
    assert "фон, задач: 6" in text
    assert "Собрать отчёт" in text and "1cv8.exe" in text


def test_pid_probe_is_honest():
    assert fleet_data._pid_alive(os.getpid()) is True
    assert fleet_data._pid_alive(DEAD_PID) is False
    assert fleet_data._pid_alive(None) is None
    assert fleet_data._pid_alive("не pid") is None
    assert fleet_data._pid_alive(0) is None


def test_broken_checkpoint_is_a_source_error(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    broken = home / "processes.json"
    broken.write_text("{это не json", encoding="utf-8")
    monkeypatch.setattr(fleet_data, "_home", lambda: home)
    snap = fleet_data.board({"language": "ru"})
    assert snap["background"]["rows"] == []
    assert any(err["path"] == str(broken) for err in snap["sources_errors"])
