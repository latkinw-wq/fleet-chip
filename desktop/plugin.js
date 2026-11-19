/**
 * fleet-chip — приборка наших процессов в статусбаре Hermes Desktop.
 *
 * Данные берёт из своей Python-половины: `ctx.rest('/board')` →
 * /api/plugins/fleet-chip/board. Никаких вызовов модели, никакой сети:
 * backend читает kanban.db / profiles\/*\/state.db / cron\/executions.db
 * в режиме mode=ro.
 *
 * Состояния (приходят готовыми с backend):
 *   working        — работает, heartbeat свежий, модель отвечает
 *   never_model    — heartbeat идёт, но вызовов модели 0 (наш класс зависания)
 *   running_silent — молчит дольше порога
 *   not_started    — карточка готова, но никто не взял
 *   blocked        — ждёт решения
 *   lost           — воркер умер, карточка осталась
 *   done_recent    — закрылась за последние часы (для счётчика)
 *
 * Чип: клик — панель со списком, правый клик — «обновить».
 * Плагин читает только свой backend; ничего не пишет.
 */
import { Tip, cn, haptic, host } from '@hermes/plugin-sdk'
import { jsx, jsxs } from 'react/jsx-runtime'
import { useCallback, useEffect, useRef, useState } from 'react'

const ID = 'fleet-chip'
// Подпись-якорь в статусбаре: 🤖 (робот) — выбран владельцем 17.11.2026.
// Меняется одной строкой: 'fleet', 'воркеры', '⌘'.
const LABEL = '🤖'
const REFRESH_MS = 5000
// Что поднимает чип в «требуют внимания». Отложенные (blocked_wait) — намеренно НЕ здесь:
// карточка, которую паркуют до своего часа, не должна держать чип жёлтым.
const PROBLEM_STATES = [
  'lost',
  'never_model',
  'api_unknown',
  'running_silent',
  'blocked_ask',
  'blocked_stale',
  'parked_stale',
  'not_started'
]

// Строки интерфейса. Язык приходит с backend (board.lang — из настроек плагина),
// пока ответа нет — navigator.language; незнакомый язык даёт английский.
// Подписи состояний живут только здесь: подсказки строк (hint) присылает backend
// уже переведёнными, чтобы чип и /fleet не разъезжались по языку.
const TEXT = {
  ru: {
    states: {
      working: 'работает',
      never_model: 'не доехал',
      api_unknown: 'не знаю (нет сессии)',
      running_silent: 'молчит',
      not_started: 'не поехала',
      blocked_ask: 'просит решения',
      blocked_stale: 'завис блок',
      blocked_wait: 'отложена',
      parked: 'отложена',
      parked_stale: 'отложена давно',
      lost: 'умер',
      done_recent: 'закрыта'
    },
    untitled: '(без названия)',
    title: 'Приборка',
    loading: 'загрузка…',
    updated: when => `обновлено ${when}`,
    backendDown: 'backend недоступен — плагин не включён в plugins.enabled?',
    empty: 'живых карточек нет',
    cron: n => `кроны: ${n}`,
    failing: n => `падают: ${n}`,
    delivery: n => `доставка не прошла за 24ч: ${n}`,
    sources: n => `источники: ${n} ошиб.`,
    justNow: 'только что',
    min: 'мин',
    hour: 'ч',
    heartbeat: 'heartbeat',
    model: 'модель',
    calls: 'выз.',
    tipAttention: list => `внимание: ${list}`,
    tipWorking: (n, live) => `работает: ${n}${live}`,
    tipLive: (id, calls) => ` (${id}, ${calls} выз.)`,
    tipWait: n => `отложено: ${n}`,
    tipCron: (n, bad) => `кроны: ${n}, сбоев ${bad}`,
    tipQuiet: 'всё тихо',
    tipHint: 'клик — приборка, правый клик — обновить'
  },
  en: {
    states: {
      working: 'working',
      never_model: 'no model',
      api_unknown: 'unknown (no session)',
      running_silent: 'silent',
      not_started: 'not started',
      blocked_ask: 'needs a decision',
      blocked_stale: 'stale block',
      blocked_wait: 'waiting',
      parked: 'parked',
      parked_stale: 'parked long',
      lost: 'dead',
      done_recent: 'closed'
    },
    untitled: '(untitled)',
    title: 'Fleet',
    loading: 'loading…',
    updated: when => `updated ${when}`,
    backendDown: 'backend unreachable — is the plugin in plugins.enabled?',
    empty: 'no live cards',
    cron: n => `cron: ${n}`,
    failing: n => `failing: ${n}`,
    delivery: n => `undelivered in 24h: ${n}`,
    sources: n => `sources: ${n} errors`,
    justNow: 'just now',
    min: 'min',
    hour: 'h',
    heartbeat: 'heartbeat',
    model: 'model',
    calls: 'calls',
    tipAttention: list => `attention: ${list}`,
    tipWorking: (n, live) => `working: ${n}${live}`,
    tipLive: (id, calls) => ` (${id}, ${calls} calls)`,
    tipWait: n => `waiting: ${n}`,
    tipCron: (n, bad) => `cron: ${n}, failing ${bad}`,
    tipQuiet: 'all quiet',
    tipHint: 'click — the board, right click — refresh'
  }
}

function langOf(board) {
  const fromBoard = board && board.lang
  if (fromBoard === 'ru' || fromBoard === 'en') return fromBoard
  const nav = (typeof navigator !== 'undefined' && navigator.language) || ''
  return /^ru\b/i.test(nav) ? 'ru' : 'en'
}

function text(lang) {
  return TEXT[lang] || TEXT.en
}

const STATE_ORDER = [
  'lost',
  'never_model',
  'api_unknown',
  'running_silent',
  'blocked_ask',
  'blocked_stale',
  'parked_stale',
  'not_started',
  'working',
  'blocked_wait',
  'parked',
  'done_recent'
]

// Три группы вместо десяти состояний: в чипе глиф обозначает ГРУППУ, а не состояние.
// Символы — монохромные (не эмодзи): ⏸/▶ имеют emoji-presentation и в статусбаре
// красятся системным шрифтом (синий «пауза»), поэтому взяты текстовые ▸ и ‖.
const BUCKET_GLYPH = { bad: '!', work: '▸', wait: '‖' }

function bucketOf(state) {
  if (state === 'working') return 'work'
  if (state === 'blocked_wait' || state === 'parked') return 'wait'
  return 'bad'
}

function ageText(min, lang) {
  const t = text(lang)
  if (min == null) return '—'
  if (min < 1) return t.justNow
  if (min < 60) return `${Math.round(min)} ${t.min}`
  const h = Math.floor(min / 60)
  const m = Math.round(min % 60)
  return m ? `${h} ${t.hour} ${m} ${t.min}` : `${h} ${t.hour}`
}

function counts(board) {
  const chip = (board && board.chip) || {}
  const problems = PROBLEM_STATES.reduce((n, s) => n + (chip[s] || 0), 0)
  const work = chip.working || 0
  const wait = (chip.blocked_wait || 0) + (chip.parked || 0)
  return { chip, problems, work, wait }
}

// Тултип бесплатен по ширине, поэтому вся детализация живёт здесь, а чип молчит.
function tipText(board, error, lang) {
  const t = text(lang)
  if (!board) return error ? `fleet-chip: ${error}` : `fleet-chip: ${t.loading}`
  const { problems, work, wait } = counts(board)
  const rows = board.rows || []
  const parts = []
  const bad = rows.filter(r => PROBLEM_STATES.includes(r.state)).slice(0, 3)
  if (bad.length) {
    parts.push(t.tipAttention(bad.map(r => `${r.id} — ${t.states[r.state] || r.state}`).join(', ')))
  }
  const live = rows.find(r => r.state === 'working')
  if (work) {
    parts.push(t.tipWorking(work, live ? t.tipLive(live.id, live.api_calls ?? '?') : ''))
  }
  if (wait) parts.push(t.tipWait(wait))
  const cron = board.cron || {}
  parts.push(t.tipCron(cron.jobs || 0, (cron.failing || []).length))
  if (!problems && !work && !wait) parts.unshift(t.tipQuiet)
  parts.push(t.tipHint)
  return parts.join(' · ')
}

function useFleet(rest) {
  const [board, setBoard] = useState(null)
  const [error, setError] = useState(null)
  const [at, setAt] = useState(0)

  const refresh = useCallback(async () => {
    try {
      const res = await rest('/board', { method: 'GET', timeoutMs: 15000 })
      if (res && res.ok === false) throw new Error(res.error || 'backend error')
      setBoard(res)
      setError(null)
    } catch (e) {
      setError(String((e && e.message) || e))
    } finally {
      setAt(Date.now())
    }
  }, [rest])

  useEffect(() => {
    let alive = true
    let timer = null
    const tick = async () => {
      if (!alive) return
      await refresh()
      if (alive) timer = setTimeout(tick, REFRESH_MS)
    }
    void tick()
    return () => {
      alive = false
      if (timer) clearTimeout(timer)
    }
  }, [refresh])

  return { board, error, at, refresh }
}

function StatePill({ state, lang }) {
  const problem = PROBLEM_STATES.includes(state)
  const t = text(lang)
  return jsxs('span', {
    className: cn(
      'inline-flex shrink-0 items-center gap-1 rounded px-1.5 py-0.5 text-[0.625rem] leading-none',
      problem ? 'text-(--ui-accent)' : 'text-(--ui-text-tertiary)'
    ),
    children: [
      jsx('span', { key: 'g', className: 'font-mono', children: BUCKET_GLYPH[bucketOf(state)] }),
      jsx('span', { key: 'l', children: t.states[state] || state })
    ]
  })
}

function Row({ row, lang }) {
  const problem = PROBLEM_STATES.includes(row.state)
  const t = text(lang)
  return jsxs('div', {
    className: cn(
      'grid grid-cols-[9rem_1fr_auto] items-baseline gap-2 border-b border-(--ui-stroke-secondary) py-1.5 text-[0.75rem] last:border-b-0'
    ),
    children: [
      jsxs('div', {
        className: 'flex min-w-0 flex-col gap-0.5',
        children: [
          jsx('span', {
            className: cn('truncate font-mono text-[0.6875rem]', problem ? 'text-(--ui-accent)' : 'text-(--ui-text-tertiary)'),
            children: row.id
          }),
          jsx(StatePill, { state: row.state, lang })
        ]
      }),
      jsxs('div', {
        className: 'flex min-w-0 flex-col gap-0.5',
        children: [
          jsx('span', { className: 'truncate text-foreground', children: row.title || t.untitled }),
          jsx('span', {
            className: 'truncate text-(--ui-text-quaternary)',
            children: [
              row.assignee || '—',
              ` · ${t.heartbeat} ${ageText(row.heartbeat_age_min, lang)}`,
              ` · ${t.model} ${row.api_calls == null ? '?' : row.api_calls} ${t.calls}`,
              ` · ${row.board || 'default'}`
            ].join('')
          })
        ]
      }),
      jsx('span', {
        className: 'shrink-0 text-(--ui-text-quaternary) tabular-nums',
        children: ageText(row.age_min, lang)
      })
    ]
  })
}

function Panel({ board, error, at, refresh, onClose, lang }) {
  const t = text(lang)
  const rows = ((board && board.rows) || []).slice().sort(
    (a, b) => STATE_ORDER.indexOf(a.state) - STATE_ORDER.indexOf(b.state)
  )
  const cron = (board && board.cron) || {}
  const srcErrors = (board && board.sources_errors) || []

  return jsxs('div', {
    className: cn(
      'fixed flex flex-col overflow-hidden rounded-lg border border-(--ui-stroke-secondary) shadow-xl'
    ),
    style: {
      right: '12px',
      bottom: '28px',
      width: '620px',
      maxHeight: '70vh',
      zIndex: 2147482001,
      background: 'var(--ui-bg-elevated, #1e1e22)'
    },
    children: [
      jsxs('div', {
        className: 'flex items-center justify-between gap-2 border-b border-(--ui-stroke-secondary) px-3 py-2',
        children: [
          jsx('span', { className: 'text-[0.8125rem] font-medium text-foreground', children: t.title }),
          jsxs('span', {
            className: 'flex items-center gap-2 text-[0.6875rem] text-(--ui-text-quaternary)',
            children: [
              jsx('span', {
                children: error ? `backend: ${error}` : at ? t.updated(new Date(at).toLocaleTimeString()) : t.loading
              }),
              jsx('button', {
                type: 'button',
                className: 'rounded px-1.5 py-0.5 text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
                onClick: () => void refresh(),
                children: '↻'
              }),
              jsx('button', {
                type: 'button',
                className: 'rounded px-1.5 py-0.5 text-(--ui-text-tertiary) hover:bg-(--chrome-action-hover) hover:text-foreground',
                onClick: onClose,
                children: '×'
              })
            ]
          })
        ]
      }),
      jsx('div', {
        className: 'min-h-0 flex-1 overflow-auto px-3 py-1',
        children: rows.length
          ? rows.map(r => jsx(Row, { row: r, lang }, r.id))
          : jsx('div', {
              className: 'py-6 text-center text-[0.75rem] text-(--ui-text-quaternary)',
              children: error ? t.backendDown : t.empty
            })
      }),
      jsxs('div', {
        className: 'flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-(--ui-stroke-secondary) px-3 py-1.5 text-[0.6875rem] text-(--ui-text-quaternary)',
        children: [
          jsx('span', { children: t.cron(cron.jobs || 0) }),
          jsx('span', {
            className: cron.failing && cron.failing.length ? 'text-(--ui-accent)' : undefined,
            children: t.failing((cron.failing || []).length)
          }),
          jsx('span', { children: t.delivery(cron.delivery_failed_24h || 0) }),
          srcErrors.length
            ? jsx('span', { className: 'text-(--ui-accent)', children: t.sources(srcErrors.length) })
            : null
        ]
      })
    ]
  })
}

function Chip({ rest }) {
  const { board, error, at, refresh } = useFleet(rest)
  const [open, setOpen] = useState(false)
  const { problems, work, wait } = counts(board)
  const lang = langOf(board)

  const onClick = () => {
    haptic('tap')
    setOpen(v => !v)
  }

  // Компактная подпись: три группы, нули не печатаются, порядок фиксирован.
  //   всё тихо              → fleet
  //   работают              → fleet ▸1
  //   проблемы              → fleet !2   (акцентом)
  //   всё сразу             → fleet !1 ▸2 ‖1
  const groups = [
    { key: 'bad', n: problems },
    { key: 'work', n: work },
    { key: 'wait', n: wait }
  ].filter(g => g.n > 0)

  return jsxs('span', {
    className: 'relative inline-flex h-full items-center',
    children: [
      jsx(Tip, {
        label: tipText(board, error, lang),
        children: jsx('button', {
          type: 'button',
          onClick,
          onContextMenu: e => {
            e.preventDefault()
            void refresh()
          },
          className: cn(
            'inline-flex h-full items-center gap-1 px-1.5 text-[0.6875rem] transition-colors',
            problems ? 'text-(--ui-accent)' : 'text-(--ui-text-tertiary)',
            'hover:bg-(--chrome-action-hover) hover:text-foreground'
          ),
          children: jsxs('span', {
            className: 'inline-flex items-baseline gap-1.5',
            children: [
              jsx('span', { children: LABEL }),
              groups.length
                ? jsx('span', {
                    className: 'inline-flex items-baseline gap-1.5 tabular-nums',
                    children: groups.map(g =>
                      jsxs('span', {
                        className: g.key === 'bad' ? 'font-medium' : undefined,
                        children: [
                          jsx('span', { className: 'font-mono opacity-70', children: BUCKET_GLYPH[g.key] }),
                          jsx('span', { children: String(g.n) })
                        ]
                      }, g.key)
                    )
                  })
                : null
            ]
          })
        })
      }),
      open
        ? jsx(Panel, { board, error, at, refresh, onClose: () => setOpen(false), lang })
        : null
    ]
  })
}

export default {
  id: ID,
  name: 'Fleet Chip',
  description: 'Приборка канбан-воркеров и кронов в статусбаре: работает / не доехал до модели / молчит / не поехала / ждёт решения.',
  register(ctx) {
    ctx.register({
      id: 'chip',
      area: 'statusBar.right',
      order: 210,
      render: () => jsx(Chip, { rest: ctx.rest })
    })
  }
}
