import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import path from "node:path"

const pExecFile = promisify(execFile)

const ENGINE_SCRIPT = path.join(import.meta.dirname, "semsearch_engine.py")
const VENV_PYTHON =
  process.env.SEMSEARCH_PYTHON ||
  `${process.env.HOME}/.local/share/venvs/semsearch/bin/python`

export default tool({
  description:
`Семантический поиск по всей истории сессий OpenCode (локально, на устройстве).

Используй ТОЛЬКО когда нужно найти прошлые сообщения пользователя с OpenCode по СМЫСЛУ, а не точному слову. Типичные поводы:
- "найди где я спрашивал про X"
- "где мы обсуждали Y"
- "покажи прошлый раз, когда я возился с Z"

Не используй для: вопросов по текущему коду, чтения/записи файлов, настройки opencode, текущей беседы — у opencode есть специализированные тулы.

На вход: запрос на естественном языке (RU/EN). На выход: список наиболее релевантных СООБЩЕНИЙ с указанием роли (user/assistant), превью и атрибуцией к сессии (id + title).`,
  args: {
    query: tool.schema.string().describe(
      "Поисковый запрос на естественном языке. Примеры: «three.js планета», «ошибка при перезагрузке mac», «где я спрашивал про провайдера alibaba»."),
    top: tool.schema.number().optional().describe(
      "Количество результатов (default: 10, max: 50)."),
    role: tool.schema.enum(["user", "assistant", "any"]).optional().describe(
      "Фильтр по автору сообщения. 'user' — только вопросы пользователя. default: 'any'."),
    since_days: tool.schema.number().optional().describe(
      "Искать только в сессиях последних N дней."),
    only_active: tool.schema.boolean().optional().describe(
      "Не показывать архивные сессии (default: true)."),
  },
  async execute(args, ctx) {
    const payload = JSON.stringify({
      query: args.query,
      top: args.top ?? 10,
      role: args.role ?? "any",
      since_days: args.since_days ?? null,
      only_active: args.only_active ?? true,
    })
    try {
      const { stdout, stderr } = await pExecFile(
        VENV_PYTHON,
        [ENGINE_SCRIPT, payload],
        { maxBuffer: 16 * 1024 * 1024, timeout: 180_000 },
      )
      if (stderr) process.stderr.write(`[semsearch] ${stderr}\n`)
      return stdout.trim()
    } catch (e: any) {
      const msg = e?.stderr || e?.message || String(e)
      throw new Error(`semsearch engine failed: ${msg.slice(0, 500)}`)
    }
  },
})
