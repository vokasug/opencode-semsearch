import { tool } from "@opencode-ai/plugin"
import { execFile } from "node:child_process"
import { promisify } from "node:util"
import path from "node:path"

const pExecFile = promisify(execFile)

const ENGINE_SCRIPT = path.join(import.meta.dirname, "semsearch_engine.py")
const VENV_PYTHON =
  process.env.SEMSEARCH_PYTHON ||
  `${process.env.HOME}/.local/share/venvs/semsearch/bin/python`
const TIMEOUT_MS = Number(process.env.SEMSEARCH_TIMEOUT_MS) || 1_200_000

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
        { maxBuffer: 16 * 1024 * 1024, timeout: TIMEOUT_MS },
      )
      if (stderr) process.stderr.write(`[semsearch] ${stderr}\n`)
      return stdout.trim()
    } catch (e: any) {
      const parts: string[] = []
      if (e?.killed || e?.signal === "SIGTERM")
        parts.push(`killed by timeout (${TIMEOUT_MS / 1000}s)`)
      // engine пишет JSON с {"error","detail"} в stdout — показываем его первым
      if (e?.stdout) parts.push(`stdout: ${e.stdout.trim().slice(0, 400)}`)
      // из stderr важен хвост (traceback), а не прогресс-бары в начале
      if (e?.stderr) parts.push(`stderr: ${e.stderr.trim().slice(-400)}`)
      if (!parts.length) parts.push(e?.message || String(e))
      throw new Error(`semsearch engine failed: ${parts.join(" | ")}`)
    }
  },
})
