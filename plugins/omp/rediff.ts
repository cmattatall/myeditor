/*
 * Rediff for oh-my-pi (omp): receive editor review feedback in the current omp session.
 * Wire contract shared with plugins/amp/readiff.ts (derived from cmattatall/revdiff's Amp plugin).
 * MIT License
 * Copyright (c) 2026 Umputun
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE.
 */
// Targets @oh-my-pi/pi-coding-agent 18.4.4 (github.com/can1357/oh-my-pi, tag v18.4.4).
// Only type imports are used, so omp's Bun loader and `node --test` can both load this file directly.
//
// omp lifecycle facts this file relies on (checked against the v18.4.4 sources):
// - One factory invocation serves one AgentSession for the whole process. /new, /resume, /fork and
//   /branch replace the session id in place and emit session_switch / session_branch on the SAME
//   runtime; nothing is shut down. The id changes before those events fire, so delivery re-checks it.
// - session_shutdown fires only when the process exits (quit, signal, /restart re-exec).
// - Factories are rebound to every subagent session (task tool, /tan); only `ctx.agent.kind === 'main'`
//   registers a connection.
// - There is no agent_settled / session_info_changed / ui_prompt_*: agent_end.willContinue marks a
//   scheduled continuation, tool_approval_requested/resolved mark approval prompts, and title changes
//   are observed through the session manager's onSessionNameChanged hook when available.
// - pi.sendUserMessage(content) without deliverAs starts a turn when idle and steers while streaming;
//   an explicit deliverAs:'steer' would only queue while idle. It returns void on admission.
import type { ExtensionAPI, ExtensionContext } from '@oh-my-pi/pi-coding-agent'

import { randomBytes } from 'node:crypto'
import { lstat, mkdir, mkdtemp, realpath, rename, rm, writeFile } from 'node:fs/promises'
import { createServer, type Server, type ServerResponse } from 'node:http'
import { homedir } from 'node:os'
import { basename, dirname, isAbsolute, join, relative, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const MAX_BODY = 1024 * 1024
const MAX_ID = 256
const HEARTBEAT_MS = 15_000
// A congested stream holds at most the socket's high-water mark plus one coalesced snapshot;
// a client that stays congested this long is presumed dead and dropped.
const STALL_MS = 4 * HEARTBEAT_MS
// `write` creates/overwrites one target; only a successful write changes it.
const WRITE_TOOLS = new Set(['write'])
// `edit` (wire name `apply_patch` in apply_patch mode) may touch several files and can fail after
// some sections were applied, so a completion counts whatever its outcome.
const EDIT_TOOLS = new Set(['edit', 'apply_patch'])
// Tools that can modify anything, even when they fail; Git decides what actually changed.
// eval runs Python/JS cells, task runs subagents in this checkout, lsp can apply renames/code actions.
const ANY_FILE_TOOLS = new Set(['bash', 'eval', 'task', 'lsp'])
// Internal URLs whose writes land in real files (conflict resolution, staged-preview `xd://resolve`).
const FILE_BACKED_SCHEMES = new Set(['conflict', 'xd'])

type ActivityState = 'idle' | 'running' | 'awaiting-approval' | 'error'
type ActivitySnapshot = {
  version: 1; root: string; thread: string; sequence: number
  state: ActivityState; title: string; tool: string | null
  files_revision: number
}
type StreamClient = {
  response: ServerResponse; pending: string | null; stalledSince: number; heartbeat: NodeJS.Timeout
}
type Outcome = { content: string; delivered: boolean }
type Connection = {
  server: Server; directory: string; descriptor: string; root: string; cwd: string; thread: string
  sequence: number; filesRevision: number; clients: Set<StreamClient>; outcomes: Map<string, Outcome>
}
type ToolRecord = { name: string; paths: string[] | null }

class HttpError extends Error {
  readonly status: number
  constructor(status: number, message: string) { super(message); this.status = status }
}

async function closeServer(server: Server): Promise<void> {
  if (!server.listening) return
  server.closeAllConnections()
  await new Promise<void>((done) => server.close(() => done()))
}

const unquote = (value: string): string => {
  const trimmed = value.trim()
  return trimmed.length > 1 && /^(["']).*\1$/.test(trimmed) ? trimmed.slice(1, -1) : trimmed
}

/** Paths a file tool will touch, from its arguments; null when the schema is not recognized. */
function toolPaths(name: string, args: unknown): string[] | null {
  if (typeof args !== 'object' || args === null) return null
  const record = args as Record<string, unknown>
  const paths: string[] = []
  const direct = record.path ?? record.file_path
  if (typeof direct === 'string') {
    paths.push(direct)
    // patch mode: { path, edits: [{ op?, rename?, diff? }] }
    if (Array.isArray(record.edits)) {
      for (const edit of record.edits) {
        const target = (edit as { rename?: unknown } | null)?.rename
        if (typeof target === 'string') paths.push(target)
      }
    }
    return paths
  }
  if (!EDIT_TOOLS.has(name) || typeof record.input !== 'string') return null
  for (const line of record.input.split(/\r?\n/)) {
    // hashline sections: `[PATH#TAG]`, `MV DEST`; apply_patch: `*** Update File: PATH`, `*** Move to: DEST`.
    const match = /^\[(.+?)(?:#[0-9A-Fa-f]{4})?\]\s*$/.exec(line) ?? /^\s*MV\s+(.+)$/.exec(line) ??
      /^\*\*\* (?:Add|Update|Delete) File:\s*(.+)$/.exec(line) ?? /^\*\*\* Move to:\s*(.+)$/.exec(line)
    if (match) paths.push(unquote(match[1]))
  }
  return paths.length ? paths : null
}

/** Resolve a tool path the way omp's resolveToCwd does, then canonicalize existing ancestors. */
async function canonicalToolPath(path: string, cwd: string): Promise<string> {
  let value = path.replace(/^:(?=[/\\~]|\.\.?[/\\])/, '')
  if (/^@(?:\/|~$|~\/)/.test(value)) value = value.slice(1)
  if (value === '~') value = homedir()
  else if (value.startsWith('~')) value = join(homedir(), value.slice(1))
  if (/^\/+$/.test(value)) value = cwd // omp treats a bare `/` as the workspace root.
  let current = resolve(cwd, value)
  const missing: string[] = []
  while (true) {
    try {
      return join(await realpath(current), ...missing.reverse())
    } catch {
      const parent = dirname(current)
      if (parent === current) return resolve(cwd, value)
      missing.push(basename(current))
      current = parent
    }
  }
}

function inside(root: string, path: string): boolean {
  const child = relative(root, path)
  return child !== '' && child !== '..' && !child.startsWith('../') && !isAbsolute(child)
}

async function touchesRoot(paths: string[], root: string, cwd: string): Promise<boolean> {
  for (const raw of paths) {
    let path = raw.replace(/^\[(.+?)(?:#[0-9A-Fa-f]{4})?\]$/, '$1')
    const scheme = /^@?([A-Za-z][A-Za-z0-9+.-]*):\/\//.exec(path)?.[1]?.toLowerCase()
    if (scheme === 'file') {
      try { path = fileURLToPath(path.replace(/^@/, '')) } catch { continue }
    } else if (scheme !== undefined) {
      if (FILE_BACKED_SCHEMES.has(scheme)) return true
      continue // Session-local resources (local://, proc://, …) are not worktree files.
    }
    try {
      if (inside(root, await canonicalToolPath(path, cwd))) return true
    } catch { /* An unresolvable path cannot be shown as a worktree change. */ }
  }
  return false
}

export default function rediffExtension(pi: ExtensionAPI): void {
  // One factory invocation is one omp extension runtime bound to one AgentSession for the process
  // lifetime. The session id, title, and cwd change underneath it; the connection follows them.
  let ctx: ExtensionContext | null = null
  let connection: Connection | null = null
  let disabled = false
  let shutdown = false
  let subagent = false
  let transition = false
  let unsubscribeTitle: (() => void) | null = null
  // Connect/rotate/teardown run one at a time, in event order.
  let lifecycle: Promise<unknown> = Promise.resolve()
  const serialize = <T>(task: () => Promise<T>): Promise<T> => {
    const next = lifecycle.then(task, task)
    lifecycle = next.catch(() => {})
    return next
  }

  // Activity is tracked for the whole runtime so a later manual connect reports the right state.
  let running = false
  let failed = false
  let title = ''
  const tools = new Map<string, ToolRecord>()
  const approvals = new Set<string>()

  const state = (): ActivityState => approvals.size ? 'awaiting-approval'
    : running || tools.size ? 'running'
    : failed ? 'error' : 'idle'
  const line = (c: Connection): string => JSON.stringify({
    version: 1, root: c.root, thread: c.thread, sequence: c.sequence,
    state: state(), title, tool: tools.size ? [...tools.values()].at(-1)!.name : null,
    files_revision: c.filesRevision,
  } satisfies ActivitySnapshot) + '\n'
  const write = (client: StreamClient, value: string): void => {
    if (client.response.destroyed || client.response.writableEnded) return
    if (client.pending !== null) {
      if (value === '\n') return // A heartbeat must not overwrite a pending state change.
      client.pending = value // Coalesce to the latest full snapshot while the socket is congested.
      return
    }
    if (!client.response.write(value)) {
      client.pending = ''
      client.stalledSince = Date.now()
    }
  }
  const publish = (): void => {
    const c = connection
    if (!c) return
    c.sequence++
    const value = line(c)
    for (const client of c.clients) write(client, value)
  }
  const subscribe = (c: Connection, response: ServerResponse): void => {
    const client = { response, pending: null, stalledSince: 0 } as StreamClient
    client.heartbeat = setInterval(() => {
      if (client.pending !== null && Date.now() - client.stalledSince >= STALL_MS) client.response.destroy()
      else write(client, '\n')
    }, HEARTBEAT_MS)
    client.heartbeat.unref()
    c.clients.add(client)
    response.on('drain', () => {
      if (client.pending === null) return
      const pending = client.pending
      client.pending = null
      if (pending) write(client, pending)
    })
    response.once('close', () => { clearInterval(client.heartbeat); c.clients.delete(client) })
    write(client, line(c))
  }
  const readTitle = (): string => {
    try { return pi.getSessionName() ?? '' } catch { return '' }
  }

  async function teardown(c: Connection): Promise<void> {
    if (connection === c) connection = null
    for (const client of c.clients) {
      clearInterval(client.heartbeat)
      client.response.end()
    }
    c.clients.clear()
    // Remove discovery first so the editor never finds a descriptor whose server is closing.
    await rm(c.directory, { recursive: true, force: true })
    await closeServer(c.server)
  }

  /** The live session identity, or null when the session can no longer be read. */
  const identity = (): { thread: string; cwd: string } | null => {
    try {
      // ctx.cwd is a snapshot taken when omp built the context; the session manager is live.
      return ctx ? { thread: ctx.sessionManager.getSessionId(), cwd: ctx.sessionManager.getCwd?.() ?? ctx.cwd } : null
    } catch {
      return null
    }
  }
  const stale = (c: Connection): boolean => {
    const live = identity()
    return !live || live.thread !== c.thread || live.cwd !== c.cwd
  }

  function deliver(c: Connection, id: string, content: string): number {
    const previous = c.outcomes.get(id)
    if (previous) {
      if (previous.content !== content) throw new HttpError(409, 'id was already used with different content')
      if (!previous.delivered) {
        throw new HttpError(500, 'feedback outcome is uncertain; check the session before reconnecting and resending')
      }
      return 204
    }
    if (shutdown || connection !== c) throw new HttpError(503, 'this omp session is shutting down')
    if (transition) throw new HttpError(503, 'this omp session is switching; retry after reconnecting')
    const live = identity()
    if (!live) throw new HttpError(503, 'this omp session is no longer active')
    if (live.thread !== c.thread || live.cwd !== c.cwd) {
      // The id changes before session_switch/branch fires; never send into the replacement.
      void follow().catch(() => {})
      throw new HttpError(409, 'connection no longer belongs to the active omp session')
    }
    const outcome: Outcome = { content, delivered: false }
    // Cache before sending: a send that throws may still have reached omp, so it must never be repeated.
    c.outcomes.set(id, outcome)
    try {
      // No deliverAs: idle starts a turn, streaming steers. void = admitted, not model completion;
      // later failures (e.g. no credentials) are reported by omp itself.
      pi.sendUserMessage(content)
      outcome.delivered = true
      return 204
    } catch {
      throw new HttpError(500, 'feedback outcome is uncertain; check the session before reconnecting and resending')
    }
  }

  async function connect(): Promise<Connection> {
    const live = identity()
    if (!live) throw new Error('the omp session is not available')
    const { thread, cwd } = live
    const root = await realpath(cwd)
    const token = randomBytes(32).toString('hex')
    const sessionTitle = readTitle()
    let c: Connection
    const server = createServer((request, response) => {
      void (async () => {
        const feedback = request.url === '/feedback'
        const events = request.url === '/events'
        if ((!feedback && !events) || (events ? request.method !== 'GET' : !['GET', 'POST'].includes(request.method ?? ''))) {
          response.writeHead(404).end('not found')
          return
        }
        if (request.headers.origin !== undefined) {
          response.writeHead(403).end('browser origins are not accepted')
          return
        }
        if (request.headers.authorization !== `Bearer ${token}`) {
          response.writeHead(401).end('unauthorized')
          return
        }
        if (connection !== c) {
          response.writeHead(503).end('this omp session is shutting down')
          return
        }
        if (events) {
          response.writeHead(200, {
            'Content-Type': 'application/x-ndjson',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
          })
          subscribe(c, response)
          return
        }
        if (request.method === 'GET') {
          response.writeHead(200, { 'Content-Type': 'application/json' })
            .end(JSON.stringify({ version: 1, root, thread }))
          return
        }
        const chunks: Buffer[] = []
        let size = 0
        for await (const chunk of request) {
          size += chunk.length
          if (size > MAX_BODY) {
            response.writeHead(413).end('body too large')
            return
          }
          chunks.push(chunk)
        }
        let body: unknown
        try {
          body = JSON.parse(Buffer.concat(chunks).toString('utf8'))
        } catch {
          response.writeHead(400).end('invalid JSON')
          return
        }
        const { id, content } = (typeof body === 'object' && body !== null && !Array.isArray(body) ? body : {}) as
          { id?: unknown; content?: unknown }
        if (typeof id !== 'string' || id.length === 0 || id.length > MAX_ID ||
          typeof content !== 'string' || content.trim().length === 0) {
          response.writeHead(400).end('expected a bounded id and nonempty content')
          return
        }
        try {
          response.writeHead(deliver(c, id, content)).end()
        } catch (error) {
          if (!(error instanceof HttpError)) throw error
          response.writeHead(error.status).end(error.message)
        }
      })().catch(() => {
        if (!response.headersSent) response.writeHead(500)
        response.end('internal error')
      })
    })
    await new Promise<void>((done, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', () => {
        server.off('error', reject)
        done()
      })
    })
    const address = server.address()
    if (!address || typeof address === 'string') {
      await closeServer(server)
      throw new Error('rediff server has no TCP address')
    }
    const directory = await (async () => {
      const registry = join(homedir(), '.cache/rediff/omp')
      await mkdir(registry, { recursive: true, mode: 0o700 })
      const info = await lstat(registry)
      if (!info.isDirectory() || (info.mode & 0o077) !== 0 || info.uid !== process.getuid?.()) {
        throw new Error('rediff connection registry must be a private directory owned by you')
      }
      return mkdtemp(join(registry, 'session-'))
    })().catch(async (error) => {
      await closeServer(server)
      throw error
    })
    const descriptor = join(directory, 'connection.json')
    c = {
      server, directory, descriptor, root, cwd, thread, sequence: 0, filesRevision: 0,
      clients: new Set(), outcomes: new Map(),
    }
    try {
      const partial = join(directory, '.connection.json.tmp')
      await writeFile(partial, JSON.stringify({
        version: 1,
        url: `http://127.0.0.1:${address.port}/feedback`,
        token,
        root,
        thread,
        capabilities: ['activity'],
        provider: 'omp',
        ...(sessionTitle ? { title: sessionTitle } : {}),
      }), { mode: 0o600, flag: 'wx' })
      // Publish atomically so discovery never reads a partial descriptor.
      await rename(partial, descriptor)
    } catch (error) {
      await teardown(c)
      throw error
    }
    return c
  }

  /** Make the connection match the live session: keep it, rotate it, or remove it. */
  const follow = (): Promise<Connection | null> => serialize(async () => {
    for (let attempt = 0; ; attempt++) {
      const current = connection
      if (shutdown || disabled || subagent || !ctx) {
        if (current) await teardown(current)
        return null
      }
      if (current && !stale(current)) return current
      // A replaced session gets a new descriptor, token, port, id cache, and streams.
      if (current) await teardown(current)
      if (attempt === 3) throw new Error('the omp session kept changing while rediff was connecting')
      const c = await connect()
      if (!shutdown && !disabled && !stale(c)) {
        connection = c
        return c
      }
      await teardown(c) // Shut down, disconnected, or the session moved again while connecting.
    }
  })
  const disconnect = (): Promise<boolean> => serialize(async () => {
    const current = connection
    if (current) await teardown(current)
    return current !== null
  })

  const endTransition = (): void => {
    transition = false
  }
  const beginTransition = (): void => {
    // Cancellation emits no follow-up event. Stay closed until a completed switch or explicit
    // /rediff-connect: a timeout cannot distinguish cancellation from a slow switch.
    transition = true
  }
  const resetActivity = (live: ExtensionContext): void => {
    tools.clear()
    approvals.clear()
    failed = false
    try { running = !live.isIdle() } catch { running = false }
    title = readTitle()
  }
  const report = (live: ExtensionContext, message: string): void => {
    try { if (live.hasUI) live.ui.notify(message, 'warning') } catch { /* UI is optional. */ }
  }

  const instructions = (c: Connection): string => [
    'Rediff is connected.',
    'In rediff, open this same checkout and run :harness connect omp.',
    `If multiple sessions match, select session ${c.thread} in the picker.`,
    'Use :harness send to compose a message, then :w to send it.',
    '',
    `Connection: ${c.descriptor}`,
    'This connection is replaced when the session changes (/new, /resume, /fork, /branch) and removed when',
    'omp exits or restarts, or /rediff-disconnect runs.',
  ].join('\n')

  pi.on('session_start', async (_event, live) => {
    if (live.agent?.kind === 'sub') {
      subagent = true // Subagents share the parent's checkout; only the main session is reviewable.
      return
    }
    ctx = live
    resetActivity(live)
    if (!unsubscribeTitle) {
      // Not part of ReadonlySessionManager's type, but present on omp 18.4.4's SessionManager.
      const manager = live.sessionManager as unknown as { onSessionNameChanged?: (listener: () => void) => () => void }
      if (typeof manager.onSessionNameChanged === 'function') {
        unsubscribeTitle = manager.onSessionNameChanged(() => { title = readTitle(); publish() })
      }
    }
    if (disabled || shutdown) return
    try {
      await follow()
    } catch (error) {
      // Registration is best effort: omp must keep working without the editor bridge.
      report(live, `rediff automatic connection failed: ${(error as Error).message}`)
    }
  })
  pi.on('session_before_switch', () => { if (!subagent) beginTransition() })
  pi.on('session_before_branch', () => { if (!subagent) beginTransition() })
  const switched = async (live: ExtensionContext): Promise<void> => {
    if (subagent) return
    endTransition()
    ctx = live
    resetActivity(live)
    const before = connection
    try {
      const after = await follow()
      if (after && after === before) publish() // Same session reloaded: refresh state on the open streams.
    } catch (error) {
      report(live, `rediff could not follow the session change: ${(error as Error).message}`)
    }
  }
  pi.on('session_switch', (_event, live) => switched(live))
  pi.on('session_branch', (_event, live) => switched(live))
  pi.on('session_shutdown', async () => {
    shutdown = true
    endTransition()
    unsubscribeTitle?.()
    unsubscribeTitle = null
    await follow()
  })
  pi.on('agent_start', () => {
    running = true
    failed = false
    if (connection && stale(connection)) void follow().catch(() => {}) // e.g. /move changed the cwd.
    publish()
  })
  pi.on('agent_end', (event) => {
    if (event.willContinue) return // A retry, compaction, or other continuation is already scheduled.
    running = false
    tools.clear()
    approvals.clear()
    const last = [...event.messages].reverse().find((message) => message.role === 'assistant') as
      { stopReason?: string } | undefined
    failed = last?.stopReason === 'error'
    title = readTitle()
    publish()
  })
  pi.on('tool_approval_requested', (event) => { approvals.add(event.toolCallId); publish() })
  pi.on('tool_approval_resolved', (event) => { if (approvals.delete(event.toolCallId)) publish() })
  pi.on('tool_execution_start', (event) => {
    tools.set(event.toolCallId, { name: event.toolName, paths: toolPaths(event.toolName, event.args) })
    publish()
  })
  pi.on('tool_execution_end', async (event) => {
    const tool = tools.get(event.toolCallId)
    const tracked = tools.delete(event.toolCallId)
    approvals.delete(event.toolCallId)
    const c = connection
    let filesChanged = false
    if (c) {
      const name = tool?.name ?? event.toolName
      const paths = tool ? tool.paths : null
      if (ANY_FILE_TOOLS.has(name)) filesChanged = true
      else if (EDIT_TOOLS.has(name) || (WRITE_TOOLS.has(name) && !event.isError)) {
        // Unknown argument shapes count as a change rather than hiding one.
        filesChanged = paths === null || await touchesRoot(paths, c.root, c.cwd)
      }
      // Retain invalidations in every snapshot, including after stream coalescing/reconnect.
      if (filesChanged && connection === c) c.filesRevision++
    }
    if (tracked || filesChanged) publish()
  })

  pi.registerCommand('rediff-connect', {
    description: 'Connect rediff editor review feedback to this omp session',
    handler: async (_args, live) => {
      if (subagent) return
      disabled = false
      endTransition()
      ctx = live
      try {
        const c = await follow()
        if (c) live.ui.notify(instructions(c), 'info')
      } catch (error) {
        live.ui.notify(`rediff connection failed: ${(error as Error).message}`, 'error')
      }
    },
  })
  pi.registerCommand('rediff-disconnect', {
    description: 'Disconnect rediff editor review feedback from this omp session',
    handler: async (_args, live) => {
      if (subagent) return
      disabled = true
      const removed = await disconnect()
      live.ui.notify(removed ? 'Rediff disconnected.' : 'Rediff is not connected to this session.', 'info')
    },
  })
}
