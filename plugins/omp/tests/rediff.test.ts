import assert from 'node:assert/strict'
import { access, chmod, mkdir, mkdtemp, readFile, readdir, realpath, rm, stat, symlink } from 'node:fs/promises'
import { request } from 'node:http'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, before, test } from 'node:test'

import extension from '../rediff.ts'

let home: string
let workspace: string
const originalHome = process.env.HOME
before(async () => {
  home = await realpath(await mkdtemp(join(tmpdir(), 'rediff-omp-')))
  process.env.HOME = home
  workspace = join(home, 'work space')
  await mkdir(join(workspace, 'src'), { recursive: true })
})
after(async () => {
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  await rm(home, { recursive: true, force: true })
})
const registry = () => join(home, '.cache/rediff/omp')
const tick = () => new Promise((resolve) => setTimeout(resolve, 0))
async function eventually<T>(probe: () => Promise<T>): Promise<T> {
  for (let attempt = 0; ; attempt++) {
    try { return await probe() } catch (error) { if (attempt >= 100) throw error }
    await new Promise((resolve) => setTimeout(resolve, 10))
  }
}

/**
 * A fake of the omp 18.4.4 ExtensionAPI surface this extension uses. Like omp, one runtime serves the
 * process: session switches mutate the session manager in place, then emit session_switch/branch.
 */
function fakeOmp(session = 'S-1', options: {
  cwd?: string; kind?: 'main' | 'sub'; send?: (content: unknown, options: unknown) => void
} = {}) {
  const handlers = new Map<string, Array<(event: any, ctx: any) => unknown>>()
  const commands = new Map<string, (args: string, ctx: any) => Promise<void>>()
  const sent: Array<[unknown, unknown]> = []
  const notifications: Array<[string, string | undefined]> = []
  const nameListeners = new Set<() => void>()
  let idle = true
  let name: string | undefined
  const runtime = {
    session,
    cwd: options.cwd ?? workspace,
    sessionManager: {
      getSessionId: () => runtime.session,
      getSessionName: () => name,
      getCwd: () => runtime.cwd,
      onSessionNameChanged(listener: () => void) {
        nameListeners.add(listener)
        return () => { nameListeners.delete(listener) }
      },
    },
  }
  const pi: any = {
    on(event: string, handler: (event: any, ctx: any) => unknown) {
      (handlers.get(event) ?? handlers.set(event, []).get(event)!).push(handler)
    },
    registerCommand(command: string, definition: { handler: (args: string, ctx: any) => Promise<void> }) {
      commands.set(command, definition.handler)
    },
    sendUserMessage(content: unknown, sendOptions?: unknown) {
      sent.push([content, sendOptions])
      options.send?.(content, sendOptions)
    },
    getSessionName() { return name },
  }
  const ctx: any = {
    cwd: runtime.cwd, // Like omp's createContext: a snapshot, unlike sessionManager.getCwd().
    sessionManager: runtime.sessionManager,
    hasUI: true,
    ui: { notify: (message: string, type?: string) => { notifications.push([message, type]) } },
    isIdle: () => idle,
    agent: { kind: options.kind ?? 'main', id: 'Main', name: 'main', depth: 0 },
  }
  extension(pi)
  const emit = async (type: string, event: Record<string, unknown> = {}) => {
    for (const handler of handlers.get(type) ?? []) await handler({ type, ...event }, ctx)
  }
  return {
    pi, ctx, runtime, handlers, commands, sent, notifications, emit, nameListeners,
    setIdle(value: boolean) { idle = value },
    setName(value: string | undefined) { name = value },
    /** Mirror SessionManager.setSessionName: update, then notify listeners synchronously. */
    rename(value: string | undefined) { name = value; for (const listener of [...nameListeners]) listener() },
    command: (command: string) => commands.get(command)!('', ctx),
    start: () => emit('session_start'),
    /** Mirror AgentSession.newSession/switchSession/fork: id changes before session_switch fires. */
    async switchTo(next: string, reason: 'new' | 'resume' | 'fork' = 'new') {
      await emit('session_before_switch', { reason })
      runtime.session = next
      await emit('session_switch', { reason, previousSessionFile: undefined })
    },
    async branchTo(next: string) {
      await emit('session_before_branch', { entryId: 'e1' })
      runtime.session = next
      await emit('session_branch', { previousSessionFile: undefined })
    },
    shutdown: () => emit('session_shutdown'),
  }
}
type Fake = ReturnType<typeof fakeOmp>

async function descriptors(): Promise<Array<{ path: string; descriptor: any }>> {
  const entries = await readdir(registry()).catch(() => [] as string[])
  return Promise.all(entries.map(async (entry) => {
    const path = join(registry(), entry, 'connection.json')
    return { path, descriptor: JSON.parse(await readFile(path, 'utf8')) }
  }))
}
async function only(session: string) {
  const matching = (await descriptors()).filter((entry) => entry.descriptor.thread === session)
  assert.equal(matching.length, 1, `expected one descriptor for ${session}`)
  return matching[0]
}

async function post(descriptor: any, body: unknown, options: { token?: string; origin?: string; raw?: string } = {}) {
  return fetch(descriptor.url, {
    method: 'POST',
    headers: {
      authorization: `Bearer ${options.token ?? descriptor.token}`,
      ...(options.origin ? { origin: options.origin } : {}),
    },
    body: options.raw ?? JSON.stringify(body),
  })
}

async function eventStream(descriptor: any) {
  const response = await fetch(descriptor.url.replace('/feedback', '/events'), {
    headers: { authorization: `Bearer ${descriptor.token}` },
  })
  assert.equal(response.status, 200)
  assert.equal(response.headers.get('content-type'), 'application/x-ndjson')
  const reader = response.body!.getReader()
  const decoder = new TextDecoder()
  let buffered = ''
  return {
    async next() {
      while (true) {
        const newline = buffered.indexOf('\n')
        if (newline >= 0) {
          const value = buffered.slice(0, newline)
          buffered = buffered.slice(newline + 1)
          if (value) return JSON.parse(value)
          continue
        }
        const chunk = await reader.read()
        assert.equal(chunk.done, false, 'activity stream closed unexpectedly')
        buffered += decoder.decode(chunk.value, { stream: true })
      }
    },
    async closed() {
      while (true) {
        const chunk = await reader.read().catch(() => ({ done: true }))
        if (chunk.done) return true
      }
    },
    cancel: () => reader.cancel().catch(() => {}),
  }
}

test('session_start registers a private omp descriptor silently and session_shutdown removes it', async () => {
  const f = fakeOmp('S-start'); f.setName('Review installer')
  assert.deepEqual([...f.commands.keys()], ['rediff-connect', 'rediff-disconnect'])
  assert.deepEqual(await descriptors(), [], 'the factory itself must not open sockets or files')
  await Promise.all([f.start(), f.start()])
  const entries = await descriptors()
  assert.equal(entries.length, 1, 'concurrent starts share one registration')
  const { path, descriptor } = entries[0]
  assert.deepEqual(Object.keys(descriptor), ['version', 'url', 'token', 'root', 'thread', 'capabilities', 'provider', 'title'])
  assert.equal(descriptor.version, 1)
  assert.equal(descriptor.provider, 'omp')
  assert.equal(descriptor.thread, 'S-start')
  assert.equal(descriptor.title, 'Review installer')
  assert.equal(descriptor.root, await realpath(workspace))
  assert.deepEqual(descriptor.capabilities, ['activity'])
  assert.match(descriptor.url, /^http:\/\/127\.0\.0\.1:\d+\/feedback$/)
  assert.match(descriptor.token, /^[0-9a-f]{64}$/)
  assert.equal((await stat(registry())).mode & 0o777, 0o700)
  assert.equal((await stat(join(path, '..'))).mode & 0o777, 0o700)
  assert.equal((await stat(path)).mode & 0o777, 0o600)
  assert.deepEqual(await readdir(join(path, '..')), ['connection.json'], 'no partial descriptor remains')
  assert.deepEqual(f.sent, [], 'registration never starts a turn')
  assert.deepEqual(f.notifications, [])

  const headers = { authorization: `Bearer ${descriptor.token}` }
  assert.equal((await fetch(descriptor.url)).status, 401)
  assert.equal((await fetch(descriptor.url, { headers: { ...headers, origin: 'https://example.com' } })).status, 403)
  const probe = await fetch(descriptor.url, { headers })
  assert.equal(probe.status, 200)
  assert.deepEqual(await probe.json(), { version: 1, root: descriptor.root, thread: 'S-start' })
  assert.deepEqual(f.sent, [], 'probing never sends')
  const stream = await eventStream(descriptor)
  await stream.next()

  await f.shutdown()
  await assert.rejects(access(path))
  assert.equal(await stream.closed(), true, 'shutdown ends activity streams')
  assert.equal(await fetch(descriptor.url).catch(() => null), null, 'the listener is closed')
  assert.equal(f.nameListeners.size, 0, 'the title listener is released')
  await f.shutdown() // idempotent
})

test('untitled sessions omit title and a failed workspace lookup does not break omp', async (t) => {
  const f = fakeOmp('S-untitled')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-untitled')
  assert.equal('title' in descriptor, false)
  const missing = fakeOmp('S-missing', { cwd: join(home, 'does-not-exist') })
  await missing.start()
  assert.equal((await descriptors()).filter((entry) => entry.descriptor.thread === 'S-missing').length, 0)
  assert.equal(missing.notifications.length, 1)
  assert.equal(missing.notifications[0][1], 'warning')
  await missing.shutdown()
})

test('subagent runtimes never register or react to commands', async () => {
  const f = fakeOmp('S-sub', { kind: 'sub' })
  await f.start()
  await f.command('rediff-connect')
  await f.emit('agent_start')
  assert.deepEqual((await descriptors()).filter((entry) => entry.descriptor.thread === 'S-sub'), [])
  assert.deepEqual(f.notifications, [])
  assert.equal(f.nameListeners.size, 0)
  await f.shutdown()
})

test('valid feedback is sent unchanged through sendUserMessage and enforces endpoint, auth, origin, and bounds', async (t) => {
  const f = fakeOmp('S-feedback')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-feedback')
  const content = '/restart is literal text, not a command'
  assert.equal((await post(descriptor, { id: 'one', content })).status, 204)
  assert.deepEqual(f.sent, [[content, undefined]], 'no deliverAs: omp starts a turn when idle and steers when busy')
  assert.equal((await post(descriptor, {}, { token: 'bad' })).status, 401)
  assert.equal((await post(descriptor, { id: 'x', content: 'x' }, { token: '' })).status, 401)
  assert.equal((await post(descriptor, { id: 'x', content: 'x' }, { origin: 'https://example.com' })).status, 403)
  assert.equal((await post(descriptor, { id: 'x', content: 'x' }, { origin: 'null' })).status, 403)
  assert.equal((await fetch(descriptor.url.replace('/feedback', '/other'), { method: 'POST' })).status, 404)
  assert.equal((await fetch(descriptor.url, { method: 'PUT', headers: { authorization: `Bearer ${descriptor.token}` } })).status, 404)
  assert.equal((await fetch(descriptor.url.replace('/feedback', '/events'), {
    method: 'POST', headers: { authorization: `Bearer ${descriptor.token}` },
  })).status, 404)
  assert.equal((await post(descriptor, { id: '', content: 'x' })).status, 400)
  assert.equal((await post(descriptor, { id: 'x'.repeat(257), content: 'x' })).status, 400)
  assert.equal((await post(descriptor, { id: 'x', content: ' ' })).status, 400)
  assert.equal((await post(descriptor, { id: 7, content: 'x' })).status, 400)
  assert.equal((await post(descriptor, ['x'])).status, 400)
  assert.equal((await post(descriptor, {}, { raw: '{' })).status, 400)
  assert.equal((await post(descriptor, {}, { raw: 'x'.repeat(1024 * 1024 + 1) })).status, 413)
  assert.equal(f.sent.length, 1, 'rejected requests never reach omp')
})

test('feedback is acknowledged on admission whether omp is idle or running', async (t) => {
  const f = fakeOmp('S-delivery')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-delivery')
  assert.equal((await post(descriptor, { id: 'idle', content: 'while idle' })).status, 204)
  f.setIdle(false)
  await f.emit('agent_start')
  assert.equal((await post(descriptor, { id: 'busy', content: 'while running' })).status, 204)
  assert.deepEqual(f.sent, [['while idle', undefined], ['while running', undefined]])
})

test('deduplicates retries, rejects conflicting ids, and caches failed or uncertain sends', async (t) => {
  let fail = false
  const f = fakeOmp('S-dedupe', { send: () => { if (fail) throw new Error('unknown outcome') } })
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-dedupe')
  const results = await Promise.all([
    post(descriptor, { id: 'same', content: 'note' }),
    post(descriptor, { id: 'same', content: 'note' }),
  ])
  assert.deepEqual(results.map((response) => response.status), [204, 204])
  assert.equal((await post(descriptor, { id: 'same', content: 'note' })).status, 204, 'retry after success is acknowledged')
  assert.equal(f.sent.length, 1, 'concurrent and later retries deliver once')
  assert.equal((await post(descriptor, { id: 'same', content: 'other' })).status, 409)
  fail = true
  assert.equal((await post(descriptor, { id: 'failed', content: 'note' })).status, 500)
  fail = false
  const retry = await post(descriptor, { id: 'failed', content: 'note' })
  assert.equal(retry.status, 500, 'an uncertain send is never repeated')
  assert.match(await retry.text(), /uncertain/)
  assert.equal((await post(descriptor, { id: 'failed', content: 'changed' })).status, 409)
  assert.equal(f.sent.length, 2, 'the failed send was attempted once')
})

test('activity streams safe snapshots for omp lifecycle events to multiple clients', async (t) => {
  const f = fakeOmp('S-live')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-live')
  const eventsURL = descriptor.url.replace('/feedback', '/events')
  assert.equal((await fetch(eventsURL)).status, 401)
  assert.equal((await fetch(eventsURL, {
    headers: { authorization: `Bearer ${descriptor.token}`, origin: 'https://example.com' },
  })).status, 403)
  const first = await eventStream(descriptor)
  const second = await eventStream(descriptor)
  const initial = await first.next()
  assert.deepEqual(initial, {
    version: 1, root: descriptor.root, thread: 'S-live', sequence: 0,
    state: 'idle', title: '', tool: null, files_revision: 0,
  })
  assert.deepEqual(await second.next(), initial)
  const both = async () => { const value = await first.next(); assert.deepEqual(await second.next(), value); return value }

  f.rename('Live review')
  assert.equal((await both()).title, 'Live review', 'session manager title changes are streamed')
  await f.emit('agent_start')
  const started = await both()
  assert.equal(started.state, 'running')
  assert.ok(started.sequence > initial.sequence)
  const secret = 'do-not-stream-this-input-or-output'
  await f.emit('tool_execution_start', { toolCallId: 'one', toolName: 'bash', args: { command: secret } })
  assert.equal((await both()).tool, 'bash')
  await f.emit('tool_execution_start', { toolCallId: 'two', toolName: 'read', args: { path: secret } })
  const reading = await both()
  assert.equal(reading.tool, 'read')
  await f.emit('tool_execution_end', { toolCallId: 'two', toolName: 'read', result: secret, isError: false })
  const stillRunning = await both()
  assert.equal(stillRunning.tool, 'bash', 'another outstanding tool keeps reporting')
  assert.equal(stillRunning.files_revision, 0, 'reads never invalidate')
  assert.ok(!JSON.stringify([started, reading, stillRunning]).includes(secret))
  await f.emit('tool_approval_requested', { sessionId: 'S-live', toolCallId: 'one', toolName: 'bash', approvalMode: 'prompt' })
  assert.equal((await both()).state, 'awaiting-approval')
  await f.emit('tool_approval_resolved', { sessionId: 'S-live', toolCallId: 'one', toolName: 'bash', approved: true })
  assert.equal((await both()).state, 'running')
  await f.emit('agent_end', { messages: [{ role: 'assistant', stopReason: 'error' }], willContinue: true })
  await f.emit('turn_start') // Not observed; proves the scheduled continuation did not publish idle.
  await f.emit('agent_end', { messages: [{ role: 'assistant', stopReason: 'error' }] })
  const failed = await both()
  assert.equal(failed.state, 'error', 'a failed run is reported once no continuation is scheduled')
  assert.equal(failed.tool, null, 'settling clears tools without terminal events')
  await f.emit('agent_start')
  assert.equal((await both()).state, 'running')
  await f.emit('agent_end', { messages: [{ role: 'assistant', stopReason: 'stop' }] })
  assert.equal((await both()).state, 'idle')

  await first.cancel()
  f.rename(undefined)
  assert.equal((await second.next()).title, '', 'a cancelled client does not affect others')
  await second.cancel()
})

test('files_revision follows omp write/edit schemas and every bash/eval/task/lsp completion', async (t) => {
  const f = fakeOmp('S-files')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-files')
  const stream = await eventStream(descriptor)
  t.after(() => stream.cancel())
  assert.equal((await stream.next()).files_revision, 0)
  let revision = 0
  const run = async (id: string, toolName: string, args: unknown, isError = false) => {
    await f.emit('tool_execution_start', { toolCallId: id, toolName, args })
    assert.equal((await stream.next()).files_revision, revision, 'do not refresh before the tool runs')
    await f.emit('tool_execution_end', { toolCallId: id, toolName, result: { content: [] }, isError })
    return (await stream.next()).files_revision
  }
  const outside = join(home, 'elsewhere.lua')
  const cases: Array<[string, unknown, boolean, boolean]> = [
    ['write', { path: 'src/new file.lua', content: 'private' }, false, true],
    ['write', { path: '[src/a.lua#1A2B]', content: 'x' }, false, true],
    ['write', { path: join(workspace, 'src/a.lua'), content: 'x' }, true, false],
    ['write', { path: '../outside.lua', content: 'x' }, false, false],
    ['write', { path: outside, content: 'x' }, false, false],
    ['write', { path: '~/elsewhere.lua', content: 'x' }, false, false],
    ['write', { path: 'local://notes.md', content: 'x' }, false, false],
    ['write', { path: 'xd://resolve', content: 'apply' }, false, true],
    ['write', { path: 'conflict://3', content: 'ours' }, false, true],
    ['write', { path: 'fixtures/data.zip:inner.txt', content: 'x' }, false, true],
    ['edit', { path: 'src/a.lua', old_string: 'a', new_string: 'b' }, false, true],
    ['edit', { path: outside, edits: [{ rename: 'src/moved.lua' }] }, false, true],
    ['edit', { path: outside, edits: [{ diff: '@@' }] }, false, false],
    ['edit', { input: '[src/a.lua#1A2B]\nPUT 1.=1:\n+[not/a/header.lua]' }, false, true],
    ['edit', { input: `[${outside}#1A2B]\nPUT 1.=1:\n+x` }, false, false],
    ['edit', { input: `[${outside}#1A2B]\nMV "src/from outside.lua"` }, false, true],
    ['edit', { input: '[src/a.lua#1A2B]\nPUT 9.=9:\n+x' }, true, true],
    ['apply_patch', { input: '*** Begin Patch\n*** Update File: src/a.lua\n@@\n-a\n+b\n*** End Patch' }, false, true],
    ['apply_patch', { input: `*** Begin Patch\n*** Delete File: ${outside}\n*** End Patch` }, false, false],
    ['edit', { input: 'unrecognized edit dialect' }, false, true],
    ['read', { path: 'src/a.lua' }, false, false],
    ['ast_edit', { ops: [{ pat: 'a', out: 'b' }], paths: ['src'] }, false, false],
    ['bash', { command: 'make' }, false, true],
    ['bash', { command: 'false' }, true, true],
    ['eval', { cells: [] }, false, true],
    ['task', { tasks: [] }, false, true],
    ['lsp', { action: 'rename' }, false, true],
    ['custom_tool', { path: 'src/a.lua' }, false, false],
  ]
  for (const [toolName, args, isError, changes] of cases) {
    const observed = await run(`${toolName}-${revision}-${String(isError)}`, toolName, args, isError)
    if (changes) revision++
    assert.equal(observed, revision, `${toolName} ${JSON.stringify(args)} isError=${isError}`)
  }
  assert.equal(revision, 17)
  // A new subscriber receives the retained revision, not just transient events.
  const reconnected = await eventStream(descriptor)
  assert.equal((await reconnected.next()).files_revision, 17)
  await reconnected.cancel()
})

test('symlinked working directories resolve tool paths against the canonical root', async (t) => {
  const link = join(home, 'linked-workspace')
  await symlink(workspace, link)
  const f = fakeOmp('S-link', { cwd: link })
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-link')
  assert.equal(descriptor.root, await realpath(workspace))
  const stream = await eventStream(descriptor)
  t.after(() => stream.cancel())
  await stream.next()
  await f.emit('tool_execution_start', { toolCallId: 'w', toolName: 'write', args: { path: join(link, 'src/x.lua') } })
  await stream.next()
  await f.emit('tool_execution_end', { toolCallId: 'w', toolName: 'write', isError: false })
  assert.equal((await stream.next()).files_revision, 1)
})

test('session switches in the same runtime rotate the connection and never route into the new session', async () => {
  const f = fakeOmp('S-old')
  await f.start()
  const old = await only('S-old')
  const oldStream = await eventStream(old.descriptor)
  await oldStream.next()
  assert.equal((await post(old.descriptor, { id: 'before', content: 'old session' })).status, 204)

  // The id changes before session_switch fires: the old descriptor refuses rather than misroutes.
  await f.emit('session_before_switch', { reason: 'new' })
  const during = await post(old.descriptor, { id: 'during', content: 'x' })
  assert.equal(during.status, 503, 'feedback is refused while a switch is in flight')
  f.runtime.session = 'S-new'
  assert.equal((await post(old.descriptor, { id: 'during', content: 'x' })).status, 503)
  await f.emit('session_switch', { reason: 'new', previousSessionFile: undefined })
  assert.equal(await oldStream.closed(), true, 'old streams end')
  await assert.rejects(access(old.path))
  assert.equal(await post(old.descriptor, { id: 'stale', content: 'x' }).catch(() => null), null, 'the old listener is closed')
  const fresh = await only('S-new')
  assert.notEqual(fresh.descriptor.token, old.descriptor.token)
  assert.notEqual(fresh.descriptor.url, old.descriptor.url)
  assert.deepEqual((await descriptors()).map((entry) => entry.descriptor.thread), ['S-new'])
  const stream = await eventStream(fresh.descriptor)
  assert.deepEqual(await stream.next(), {
    version: 1, root: fresh.descriptor.root, thread: 'S-new', sequence: 0,
    state: 'idle', title: '', tool: null, files_revision: 0,
  })
  assert.equal((await post(fresh.descriptor, { id: 'before', content: 'new session' })).status, 204, 'a new id cache')
  assert.deepEqual(f.sent, [['old session', undefined], ['new session', undefined]])

  // A same-session reload (ctx.reload / resume of the current file) keeps the connection.
  f.rename('Reloaded')
  assert.equal((await stream.next()).title, 'Reloaded')
  await f.switchTo('S-new', 'resume')
  assert.equal((await stream.next()).thread, 'S-new', 'same-session reload republishes on the open stream')
  assert.equal((await only('S-new')).path, fresh.path)

  // Fork and branch also replace the id in place.
  await f.switchTo('S-fork', 'fork')
  assert.equal(await stream.closed(), true)
  const forked = await only('S-fork')
  assert.equal(forked.descriptor.title, 'Reloaded', 'the descriptor carries the current title')
  await f.branchTo('S-branch')
  assert.deepEqual((await descriptors()).map((entry) => entry.descriptor.thread), ['S-branch'])
  const branched = await only('S-branch')

  // Defensive: an id change without an event yet is refused, and the connection follows the session.
  f.runtime.session = 'S-unannounced'
  assert.equal((await post(branched.descriptor, { id: 'misrouted', content: 'x' })).status, 409)
  await eventually(() => only('S-unannounced'))
  assert.equal(f.sent.length, 2, 'nothing was sent into a session the editor did not choose')
  await f.shutdown()
  assert.deepEqual(await descriptors(), [])
})

test('a slow or cancelled switch stays closed until explicit reconnect', async (t) => {
  const f = fakeOmp('S-cancel')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-cancel')
  t.mock.timers.enable({ apis: ['setTimeout'] })
  await f.emit('session_before_switch', { reason: 'new' }) // Another extension cancels: no session_switch.
  assert.equal((await post(descriptor, { id: 'a', content: 'x' })).status, 503)
  t.mock.timers.tick(60_000)
  assert.equal((await post(descriptor, { id: 'a', content: 'x' })).status, 503)
  await f.command('rediff-connect')
  assert.equal((await post(descriptor, { id: 'a', content: 'x' })).status, 204)
  assert.deepEqual(f.sent, [['x', undefined]])
})

test('a cwd change (/move) re-registers under the new root', async (t) => {
  const f = fakeOmp('S-move')
  t.after(() => f.shutdown())
  await f.start()
  const before = await only('S-move')
  const moved = join(home, 'moved')
  await mkdir(moved)
  f.runtime.cwd = moved
  assert.equal((await post(before.descriptor, { id: 'x', content: 'x' })).status, 409)
  const after = await eventually(async () => {
    const entry = await only('S-move')
    assert.equal(entry.descriptor.root, moved)
    return entry
  })
  assert.equal(after.descriptor.root, moved)
  assert.deepEqual(f.sent, [])
})

test('shutdown during registration leaves no descriptor or listener behind', async () => {
  const f = fakeOmp('S-race')
  const starting = f.start()
  await f.shutdown()
  await starting
  await tick()
  assert.deepEqual((await descriptors()).filter((entry) => entry.descriptor.thread === 'S-race'), [])
})

test('commands show setup without secrets, disconnect suppresses following, connect restores it', async () => {
  const f = fakeOmp('S-commands')
  await f.start()
  const auto = await only('S-commands')
  await f.command('rediff-connect')
  assert.equal((await only('S-commands')).path, auto.path, 'manual connect reuses the automatic connection')
  const [message, type] = f.notifications.at(-1)!
  assert.equal(type, 'info')
  assert.match(message, /:harness connect omp/)
  assert.match(message, /select session S-commands/)
  assert.ok(message.includes(`Connection: ${auto.path}`))
  assert.ok(!message.includes(auto.descriptor.token), 'never publish the authentication token')
  assert.deepEqual(f.sent, [], 'setup information is not sent to the model')

  await f.command('rediff-disconnect')
  assert.equal(f.notifications.at(-1)![0], 'Rediff disconnected.')
  await assert.rejects(access(auto.path))
  await f.switchTo('S-commands-2')
  assert.deepEqual((await descriptors()).filter((entry) => entry.descriptor.thread.startsWith('S-commands')), [],
    'disconnect suppresses automatic registration for later sessions in this runtime')
  await f.command('rediff-disconnect')
  assert.match(f.notifications.at(-1)![0], /not connected/)
  await f.command('rediff-connect')
  const restored = await only('S-commands-2')
  assert.notEqual(restored.descriptor.token, auto.descriptor.token)
  await f.shutdown()
  assert.deepEqual((await descriptors()).filter((entry) => entry.descriptor.thread.startsWith('S-commands')), [])
})

test('a public registry is rejected without crashing omp', async () => {
  const f = fakeOmp('S-unsafe')
  await mkdir(registry(), { recursive: true })
  await chmod(registry(), 0o755)
  try {
    await f.start()
    assert.deepEqual(await readdir(registry()), [])
    assert.match(f.notifications.at(-1)![0], /private directory/)
    await f.command('rediff-connect')
    assert.equal(f.notifications.at(-1)![1], 'error')
  } finally {
    await chmod(registry(), 0o700)
    await f.shutdown()
  }
})

test('a stalled activity client gets coalesced snapshots instead of unbounded buffering', async (t) => {
  const f: Fake = fakeOmp('S-backpressure')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-backpressure')
  const url = new URL(descriptor.url.replace('/feedback', '/events'))
  const total = 400
  const name = 'x'.repeat(16 * 1024)
  // A raw client that stops reading lets the server observe real socket backpressure.
  const lines = await new Promise<string[]>((resolve, reject) => {
    const req = request(url, { headers: { authorization: `Bearer ${descriptor.token}` } }, (response) => {
      response.pause()
      response.once('close', () => reject(new Error('the server dropped a briefly congested client')))
      void (async () => {
        for (let i = 0; i < total; i++) {
          f.rename(`${name}${i}`)
          if (i % 20 === 0) await new Promise((r) => setTimeout(r, 1))
        }
        const received: string[] = []
        let buffered = ''
        response.setEncoding('utf8')
        response.on('data', (chunk: string) => {
          buffered += chunk
          let newline
          while ((newline = buffered.indexOf('\n')) >= 0) {
            const value = buffered.slice(0, newline)
            buffered = buffered.slice(newline + 1)
            if (!value) continue
            received.push(JSON.parse(value).title)
            if (received.at(-1) === `${name}${total - 1}`) { response.destroy(); resolve(received) }
          }
        })
        response.resume()
      })().catch(reject)
    })
    req.once('error', reject)
    req.end()
  })
  assert.ok(lines.length < total, `stalled client received ${lines.length} of ${total + 1} snapshots`)
  assert.equal(lines.at(-1), `${name}${total - 1}`, 'the latest snapshot is never lost')
  await tick()
  // A healthy client still works after the stalled one disconnects.
  const stream = await eventStream(descriptor)
  assert.equal((await stream.next()).title, `${name}${total - 1}`)
  await stream.cancel()
})

test('activity streams send heartbeats and drop clients that stay congested', async (t) => {
  const f: Fake = fakeOmp('S-heartbeat')
  t.after(() => f.shutdown())
  await f.start()
  const { descriptor } = await only('S-heartbeat')
  const url = new URL(descriptor.url.replace('/feedback', '/events'))
  const open = () => new Promise<import('node:http').IncomingMessage>((resolve, reject) => {
    const req = request(url, { headers: { authorization: `Bearer ${descriptor.token}` } }, resolve)
    req.once('error', reject)
    req.end()
  })
  // Only the heartbeat interval and clock are mocked; sockets and other timers stay real.
  t.mock.timers.enable({ apis: ['setInterval', 'Date'] })
  const healthy = await open()
  healthy.setEncoding('utf8')
  let text = ''
  healthy.on('data', (chunk: string) => { text += chunk })
  const stalled = await open()
  stalled.pause()
  let stalledClosed = false
  stalled.socket.once('close', () => { stalledClosed = true })
  stalled.on('error', () => {})
  const settle = () => new Promise((resolve) => setTimeout(resolve, 20))
  await settle()
  assert.equal(text.split('\n').filter(Boolean).length, 1, 'initial snapshot')
  // Congest the paused client with real socket backpressure.
  for (let i = 0; i < 400; i++) {
    f.rename(`${'y'.repeat(16 * 1024)}${i}`)
    if (i % 20 === 0) await new Promise((resolve) => setTimeout(resolve, 1))
  }
  await settle()
  text = ''
  t.mock.timers.tick(15_000)
  await settle()
  assert.equal(text, '\n', 'a heartbeat newline keeps the healthy stream alive')
  assert.equal(stalledClosed, false, 'brief congestion is tolerated')
  t.mock.timers.tick(60_000)
  await settle()
  // A paused client cannot notice EOF; once it reads again it must find the server hung up.
  stalled.resume()
  for (let i = 0; i < 50 && !stalledClosed; i++) await settle()
  assert.equal(stalledClosed, true, 'a client congested past the stall limit is dropped')
  assert.ok(!healthy.destroyed, 'healthy clients are unaffected')
  f.rename('still live')
  await settle()
  assert.match(text, /"title":"still live"/)
  healthy.destroy()
})
