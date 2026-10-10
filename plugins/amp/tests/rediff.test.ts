import assert from 'node:assert/strict'
import { execFile, fork } from 'node:child_process'
import { createHash } from 'node:crypto'
import { once } from 'node:events'
import { access, chmod, mkdtemp, readFile, readdir, realpath, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { promisify } from 'node:util'

import plugin from '../readiff.ts'

type Command = (ctx: any) => Promise<void>

class ObservableMock<T> {
  readonly observers = new Set<(value: T) => void>()
  private value: T
  private readonly failure?: Error
  constructor(value: T, failure?: Error) { this.value = value; this.failure = failure }
  async get(): Promise<T> { if (this.failure) throw this.failure; return this.value }
  subscribe(observer: ((value: T) => void) | { next?: (value: T) => void }) {
    const next = typeof observer === 'function' ? observer : (value: T) => observer.next?.(value)
    this.observers.add(next)
    return { unsubscribe: () => this.observers.delete(next) }
  }
  emit(value: T) { this.value = value; for (const observer of this.observers) observer(value) }
}

let home: string
const originalHome = process.env.HOME
const originalState = process.env.XDG_STATE_HOME
before(async () => {
  home = await mkdtemp(join(tmpdir(), 'rediff-plugin-'))
  process.env.HOME = home
  process.env.XDG_STATE_HOME = join(home, 'state')
})
after(async () => {
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  if (originalState === undefined) delete process.env.XDG_STATE_HOME
  else process.env.XDG_STATE_HOME = originalState
  await rm(home, { recursive: true, force: true })
})

function fakeAmp(root = process.cwd()) {
  const commands = new Map<string, Command>()
  const events = new Map<string, (event: any, ctx: any) => any>()
  const disposers: Array<() => Promise<void>> = []
  const messages = new Map<string, any[]>()
  const notifications: string[] = []
  const threads = new Map<string, any>()
  const amp: any = {
    helpers: { filePathFromURI: fileURLToPath, filesModifiedByToolCall: () => null },
    logger: { log() {} },
    on(event: string, handler: (event: unknown, ctx: any) => any) { events.set(event, handler); return { unsubscribe() {} } },
    registerCommand(id: string, _options: unknown, handler: Command) { commands.set(id, handler); return { unsubscribe() {} } },
    onDispose(handler: () => Promise<void>) { disposers.push(handler); return { unsubscribe() {} } },
  }
  const context = (id: string, append?: (...args: any[]) => Promise<void>) => ({
    thread: threads.get(id) ?? threads.set(id, {
      id, title: new ObservableMock<string | null>(null), state: new ObservableMock('idle'), appendUserMessage: async (...args: any[]) => {
      (messages.get(id) ?? messages.set(id, []).get(id)!).push(args)
      if (args[1]?.steer) await append?.(...args)
      },
    }).get(id),
    system: { workspaceRoot: pathToFileURL(root) },
    ui: { notify: async (value: string) => { notifications.push(value) } },
  })
  return { amp, commands, events, disposers, messages, notifications, threads, context }
}

async function connect(f: ReturnType<typeof fakeAmp>, id: string, append?: (...args: any[]) => Promise<void>) {
  await f.commands.get('readiff-connect')!(f.context(id, append))
  const command = f.messages.get(id)!.at(-1)![0].content
  const path = command.match(/^Connection: (.+)$/m)![1]
  return { path, descriptor: JSON.parse(await readFile(path, 'utf8')) }
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
    cancel: () => reader.cancel(),
  }
}

test('connect posts editor instructions to its thread, reuses connections, and cleans up', async () => {
  const f = fakeAmp(); await plugin(f.amp)
  assert.deepEqual([...f.commands.keys()], ['readiff-connect', 'readiff-disconnect'])
  await Promise.all([
    f.commands.get('readiff-connect')!(f.context('T-a')),
    f.commands.get('readiff-connect')!(f.context('T-a')),
  ])
  assert.equal(f.messages.get('T-a')!.length, 2, 'each explicit connect shows the command')
  assert.deepEqual(f.notifications, [], 'the launch command must not be a transient popup')
  const [announcement, options] = f.messages.get('T-a')![0]
  assert.equal(announcement.type, 'user-message')
  assert.match(announcement.content, /:harness connect amp/)
  assert.match(announcement.content, /not a request for the agent to run commands/)
  assert.match(announcement.content, /select thread T-a/)
  assert.equal(options, undefined, 'setup is not steering feedback')
  const a = await connect(f, 'T-a'); const b = await connect(f, 'T-b')
  assert.notEqual(a.path, b.path); assert.notEqual(a.descriptor.token, b.descriptor.token)
  assert.deepEqual(Object.keys(a.descriptor), ['version', 'url', 'token', 'root', 'thread', 'capabilities'])
  assert.deepEqual(a.descriptor.capabilities, ['activity'])
  assert.equal(a.descriptor.thread, 'T-a'); assert.equal((await stat(a.path)).mode & 0o777, 0o600)
  const again = await connect(f, 'T-a'); assert.equal(again.path, a.path)
  assert.equal(f.messages.get('T-a')!.at(-1)![0].content, announcement.content)
  assert.ok(!announcement.content.includes(a.descriptor.token), 'never publish the authentication token')
  assert.ok(!f.messages.get('T-b')![0][0].content.includes(a.path), 'commands stay in their own threads')
  assert.deepEqual(f.notifications, [], 'repeated connect also avoids the popup')
  await f.commands.get('readiff-disconnect')!(f.context('T-a'))
  await assert.rejects(access(a.path)); assert.equal((await fetch(a.descriptor.url).catch(() => null)), null)
  await Promise.all(f.disposers.map((dispose) => dispose()))
  await assert.rejects(access(b.path))
})

test('last started thread survives shutdown and is isolated by canonical worktree', async (t) => {
  const root = await realpath(await mkdtemp(join(home, 'history-')))
  const other = await realpath(await mkdtemp(join(home, 'other-')))
  const f = fakeAmp(root), g = fakeAmp(other)
  await plugin(f.amp); await plugin(g.amp)
  t.after(() => Promise.all([...f.disposers, ...g.disposers].map((dispose) => dispose())))
  const history = (path: string) => join(process.env.XDG_STATE_HOME!, 'rediff/amp', createHash('sha256').update(path).digest('hex') + '.json')
  await f.events.get('session.start')!({}, f.context('T-first'))
  await f.events.get('session.start')!({}, f.context('T-second'))
  await g.events.get('session.start')!({}, g.context('T-other'))
  await f.events.get('agent.start')!({}, f.context('T-first'))
  assert.deepEqual(JSON.parse(await readFile(history(root), 'utf8')), { root, thread: 'T-second' }, 'another turn must not rewrite launch order')
  await f.events.get('session.start')!({}, f.context('T-first'))
  await Promise.all(f.disposers.map((dispose) => dispose()))
  assert.deepEqual(JSON.parse(await readFile(history(root), 'utf8')), { root, thread: 'T-first' })
  assert.deepEqual(JSON.parse(await readFile(history(other), 'utf8')), { root: other, thread: 'T-other' })
  assert.equal((await stat(history(root))).mode & 0o777, 0o600)
})

test('thread titles are optional metadata and a failed lookup still connects', async (t) => {
  const f = fakeAmp(); await plugin(f.amp)
  t.after(() => Promise.all(f.disposers.map((dispose) => dispose())))
  for (const title of ['Review installer', null, new Error('title unavailable')]) {
    const id = `T-${title instanceof Error ? 'failed' : title ? 'titled' : 'untitled'}`
    const ctx = f.context(id)
    ctx.thread.title.get = async () => {
      if (title instanceof Error) throw title
      return title
    }
    await f.events.get('session.start')!({}, ctx)
    const { descriptor } = await connect(f, id)
    assert.equal(descriptor.thread, id)
    assert.equal(descriptor.title, typeof title === 'string' ? title : undefined)
    assert.equal((await post(descriptor, { id: 'review', content: 'still works' })).status, 204)
  }
})

test('valid feedback steers unchanged and enforces endpoint, auth, origin, and input bounds', async () => {
  const f = fakeAmp(); await plugin(f.amp); const c = await connect(f, 'T-a')
  assert.equal((await post(c.descriptor, { id: 'one', content: 'fix this' })).status, 204)
  const [message, options] = f.messages.get('T-a')![1]
  assert.equal(options.steer, true)
  assert.equal(message.content, 'fix this')
  assert.equal((await post(c.descriptor, {}, { token: 'bad' })).status, 401)
  assert.equal((await post(c.descriptor, {}, { origin: 'https://example.com' })).status, 403)
  assert.equal((await fetch(c.descriptor.url.replace('/feedback', '/other'), { method: 'POST' })).status, 404)
  assert.equal((await post(c.descriptor, { id: '', content: 'x' })).status, 400)
  assert.equal((await post(c.descriptor, { id: 'x'.repeat(257), content: 'x' })).status, 400)
  assert.equal((await post(c.descriptor, { id: 'x', content: ' ' })).status, 400)
  assert.equal((await post(c.descriptor, {}, { raw: '{' })).status, 400)
  assert.equal((await post(c.descriptor, {}, { raw: 'x'.repeat(1024 * 1024 + 1) })).status, 413)
  await Promise.all(f.disposers.map((dispose) => dispose()))
})

test('activity streams full safe snapshots to multiple clients and clean up', async () => {
  const f = fakeAmp(); await plugin(f.amp); const c = await connect(f, 'T-live')
  const eventsURL = c.descriptor.url.replace('/feedback', '/events')
  assert.equal((await fetch(eventsURL)).status, 401)
  assert.equal((await fetch(eventsURL, {
    headers: { authorization: `Bearer ${c.descriptor.token}`, origin: 'https://example.com' },
  })).status, 403)

  const first = await eventStream(c.descriptor)
  const second = await eventStream(c.descriptor)
  const initial = await first.next()
  assert.deepEqual(initial, {
    version: 1, root: c.descriptor.root, thread: 'T-live', sequence: 0,
    state: 'idle', title: '', tool: null, files_revision: 0,
  })
  assert.deepEqual(await second.next(), initial)

  const thread = f.threads.get('T-live')
  thread.title.emit('Live review')
  assert.equal((await first.next()).title, 'Live review')
  assert.equal((await second.next()).title, 'Live review')
  thread.state.emit('awaiting-approval')
  assert.equal((await first.next()).state, 'awaiting-approval')
  assert.equal((await second.next()).state, 'awaiting-approval')

  const secret = 'do-not-stream-this-input-or-output'
  const ctx = f.context('T-live')
  assert.deepEqual(await f.events.get('tool.call')!({
    thread: { id: 'T-live' }, toolUseID: 'one', tool: 'shell_command', input: { secret },
  }, ctx), { action: 'allow' })
  const approvalTool = await first.next()
  await second.next()
  assert.equal(approvalTool.state, 'awaiting-approval')
  assert.equal(approvalTool.tool, 'shell_command')
  assert.ok(!JSON.stringify(approvalTool).includes(secret))
  thread.state.emit('running')
  await first.next(); await second.next()
  await f.events.get('tool.call')!({
    thread: { id: 'T-live' }, toolUseID: 'two', tool: 'read_file', input: { secret },
  }, ctx)
  await first.next(); await second.next()
  await f.events.get('tool.result')!({
    thread: { id: 'T-live' }, toolUseID: 'two', tool: 'read_file', status: 'done', output: secret,
  }, ctx)
  const stillRunning = await first.next()
  await second.next()
  assert.equal(stillRunning.state, 'running', 'another outstanding tool keeps the agent running')
  assert.equal(stillRunning.tool, 'shell_command')
  assert.ok(!JSON.stringify(stillRunning).includes(secret))
  await f.events.get('tool.result')!({
    thread: { id: 'T-live' }, toolUseID: 'one', tool: 'shell_command', status: 'done', output: secret,
  }, ctx)
  const noTool = await first.next(); await second.next()
  assert.equal(noTool.state, 'running', 'tool completion does not invent an idle state')
  assert.equal(noTool.tool, null)
  thread.state.emit('idle')
  assert.equal((await first.next()).state, 'idle')
  assert.equal((await second.next()).state, 'idle')

  await f.events.get('tool.call')!({
    thread: { id: 'T-live' }, toolUseID: 'cancelled', tool: 'shell_command', input: {},
  }, ctx)
  await first.next(); await second.next()
  thread.state.emit('error')
  const failed = await first.next(); await second.next()
  assert.equal(failed.state, 'error')
  assert.equal(failed.tool, null, 'a failed turn clears tools without terminal result events')
  thread.state.emit('idle')
  assert.equal((await first.next()).state, 'idle')
  assert.equal((await second.next()).tool, null)

  await first.cancel()
  await new Promise((resolve) => setTimeout(resolve, 0))
  assert.equal(thread.state.observers.size, 1, 'one shared thread subscription remains for all clients')
  await f.commands.get('readiff-disconnect')!(ctx)
  assert.equal(thread.state.observers.size, 0)
  assert.equal(thread.title.observers.size, 0)
  await second.cancel().catch(() => {})
})

test('completed file tools invalidate only their worktree and survive reconnects', async (t) => {
  const f = fakeAmp(); await plugin(f.amp)
  t.after(() => Promise.all(f.disposers.map((dispose) => dispose())))
  const c = await connect(f, 'T-files')
  const other = await connect(f, 'T-other-files')
  const stream = await eventStream(c.descriptor)
  t.after(() => stream.cancel())
  assert.equal((await stream.next()).files_revision, 0)
  let paths: URL[] | null = [pathToFileURL(join(c.descriptor.root, 'space %.lua'))]
  const observed: unknown[] = []
  f.amp.helpers.filesModifiedByToolCall = (event: unknown) => { observed.push(event); return paths }
  const call = { thread: { id: 'T-files' }, toolUseID: 'edit', tool: 'apply_patch', input: { patch: 'private source' } }
  await f.events.get('tool.call')!(call, f.context('T-files'))
  assert.equal((await stream.next()).files_revision, 0, 'do not refresh before the tool edits')
  assert.deepEqual(observed, [])
  for (const status of ['done', 'error', 'cancelled']) {
    const result = { ...call, status, output: 'private output' }
    await f.events.get('tool.result')!(result, f.context('T-files'))
    const snapshot = await stream.next()
    assert.equal(snapshot.files_revision, observed.length)
    assert.equal(observed.at(-1), result, 'use the official helper on the terminal result')
    assert.ok(!JSON.stringify(snapshot).includes('private'))
    assert.ok(!JSON.stringify(snapshot).includes('space %'))
  }
  for (paths of [null, [], [pathToFileURL(c.descriptor.root + '-other/file.lua')]]) {
    await f.events.get('tool.result')!(call, f.context('T-files'))
  }
  // A new subscriber receives the retained revision, not just transient path events.
  const reconnected = await eventStream(c.descriptor)
  assert.equal((await reconnected.next()).files_revision, 3)
  await reconnected.cancel()
  const isolated = await eventStream(other.descriptor)
  assert.equal((await isolated.next()).files_revision, 0)
  await isolated.cancel()
})

test('activity changes during initial reads take precedence over stale snapshots', async (t) => {
  const f = fakeAmp(); await plugin(f.amp)
  t.after(() => Promise.all(f.disposers.map((dispose) => dispose())))
  const ctx = f.context('T-starting')
  ctx.thread.state.get = async () => {
    ctx.thread.state.emit('running')
    return 'idle'
  }
  const c = await connect(f, 'T-starting')
  const stream = await eventStream(c.descriptor)
  assert.equal((await stream.next()).state, 'running')
  await stream.cancel()
})

test('deduplicates concurrent feedback, detects conflicts, and caches ambiguous failures', async () => {
  let calls = 0; let release!: () => void
  const gate = new Promise<void>((resolve) => { release = resolve })
  const append = async () => { calls++; await gate }
  const f = fakeAmp(); await plugin(f.amp); const c = await connect(f, 'T-a', append)
  const p1 = post(c.descriptor, { id: 'same', content: 'note' })
  const p2 = post(c.descriptor, { id: 'same', content: 'note' })
  await new Promise((resolve) => setTimeout(resolve, 20)); assert.equal(calls, 1); release()
  assert.deepEqual([(await p1).status, (await p2).status], [204, 204])
  assert.equal((await post(c.descriptor, { id: 'same', content: 'other' })).status, 409)
  let failures = 0
  const bad = fakeAmp(); await plugin(bad.amp); const d = await connect(bad, 'T-b', async () => { failures++; throw new Error('unknown outcome') })
  assert.equal((await post(d.descriptor, { id: 'failed', content: 'note' })).status, 500)
  assert.equal((await post(d.descriptor, { id: 'failed', content: 'note' })).status, 500)
  assert.equal(failures, 1)
  await Promise.all([...f.disposers, ...bad.disposers].map((dispose) => dispose()))
})

test('session start registers silently, probes authenticate, and disconnect stays disabled', async (t) => {
  const f = fakeAmp(); await plugin(f.amp)
  t.after(() => Promise.all(f.disposers.map((dispose) => dispose())))
  const ctx = f.context('T-auto')
  await Promise.all([
    f.events.get('session.start')!({}, ctx),
    f.events.get('agent.start')!({}, ctx),
  ])
  const registry = join(home, '.cache/rediff/amp')
  const entries = await readdir(registry)
  assert.equal(entries.length, 1, 'concurrent events share one registration')
  const directory = join(registry, entries[0])
  const path = join(directory, 'connection.json')
  const descriptor = JSON.parse(await readFile(path, 'utf8'))
  assert.equal((await stat(registry)).mode & 0o777, 0o700)
  assert.equal((await stat(directory)).mode & 0o777, 0o700)
  assert.equal((await stat(path)).mode & 0o777, 0o600)
  assert.equal(descriptor.root, await realpath(process.cwd()))
  assert.equal(f.messages.size, 0, 'automatic registration never starts an agent turn')
  assert.deepEqual(f.notifications, [])

  assert.equal((await fetch(descriptor.url)).status, 401)
  const headers = { authorization: `Bearer ${descriptor.token}` }
  assert.equal((await fetch(descriptor.url, { headers: { ...headers, origin: 'https://example.com' } })).status, 403)
  const probe = await fetch(descriptor.url, { headers })
  assert.equal(probe.status, 200)
  assert.deepEqual(await probe.json(), { version: 1, root: descriptor.root, thread: 'T-auto' })
  assert.equal(f.messages.size, 0, 'probing never appends feedback')
  const explicit = await connect(f, 'T-auto')
  assert.equal(explicit.path, path, 'manual connect reuses the automatic connection')
  assert.equal((await post(descriptor, { id: 'review', content: 'automatic feedback' })).status, 204)
  assert.match(f.messages.get('T-auto')!.at(-1)![0].content, /automatic feedback/)

  await f.commands.get('readiff-disconnect')!(ctx)
  await f.events.get('agent.start')!({}, ctx)
  await f.events.get('session.start')!({}, ctx)
  assert.deepEqual(await readdir(registry), [], 'disconnect suppresses automatic reconnect until manual connect or reload')
  await connect(f, 'T-auto')
  assert.equal((await readdir(registry)).length, 1)
})

test('automatic registration skips missing workspaces and rejects a public registry', async (t) => {
  const f = fakeAmp(); await plugin(f.amp)
  t.after(() => Promise.all(f.disposers.map((dispose) => dispose())))
  const registry = join(home, '.cache/rediff/amp')
  const ctx = { ...f.context('T-none'), system: { workspaceRoot: null } }
  await f.events.get('session.start')!({}, ctx)
  assert.deepEqual(await readdir(registry), [])
  await chmod(registry, 0o755)
  try {
    await f.events.get('session.start')!({}, f.context('T-unsafe'))
    assert.deepEqual(await readdir(registry), [])
    await assert.rejects(connect(f, 'T-unsafe'), /private directory/)
  } finally {
    await chmod(registry, 0o700)
  }
})

test('discovers and sends across independent terminal hosts and worktrees', { timeout: 15000 }, async (t) => {
  const root = await realpath(process.cwd())
  const otherRoot = await realpath(await mkdtemp(join(home, 'other-worktree-')))
  const hosts = []
  for (const [id, directory, terminal] of [
    ['T-terminal-one', root, 'Apple_Terminal'],
    ['T-terminal-two', otherRoot, 'iTerm.app'],
  ]) {
    const child = fork(fileURLToPath(new URL('./terminal-host.ts', import.meta.url)), [directory, id], {
      cwd: directory,
      env: { ...process.env, HOME: home, TERM_PROGRAM: terminal, TMUX: `/different-${id}`, XDG_CACHE_HOME: join(home, id) },
      stdio: ['ignore', 'ignore', 'inherit', 'ipc'],
    })
    t.after(async () => {
      if (child.exitCode !== null) return
      const exited = once(child, 'exit')
      child.send('dispose')
      await exited
    })
    const [ready] = await once(child, 'message')
    assert.equal(ready.ready, true)
    hosts.push(child)
  }
  const bridge = process.env.REDIFF_TEST_BRIDGE ?? fileURLToPath(new URL('../../../nix/amp_live.py', import.meta.url))
  const run = async (...args: string[]) => JSON.parse((await promisify(execFile)('python3', ['-B', bridge, ...args], {
    cwd: root, env: { ...process.env, HOME: home, TERM_PROGRAM: 'WezTerm', TMUX: '', XDG_CACHE_HOME: join(home, 'editor-cache') },
  })).stdout)
  const all = await run('discover', '--all')
  assert.deepEqual(all.map((entry: any) => entry.session).sort(), ['T-terminal-one', 'T-terminal-two'])
  assert.deepEqual((await run('discover', root)).map((entry: any) => entry.session), ['T-terminal-one'])
  const target = all.find((entry: any) => entry.session === 'T-terminal-two')
  const payload = join(home, 'cross-terminal.json')
  await writeFile(payload, JSON.stringify({
    submission_id: 'cross-terminal', repository: root, message: 'Review your own checkout.',
    sender: { instance: 'editor-one', pid: process.pid, app: 'rediff', directory: root, repository: root },
    recipient: { provider: 'amp', id: target.session, repository: otherRoot },
  }))
  const received = once(hosts[1], 'message')
  assert.equal((await run('send', target.connection, target.session, payload)).status, 'accepted')
  const [{ message }] = await received
  const content = JSON.parse(message.content)
  assert.equal(content.sender.directory, root)
  assert.equal(content.recipient.repository, otherRoot)
  assert.equal(content.message, 'Review your own checkout.')
  const exited = once(hosts[1], 'exit')
  hosts[1].send('dispose')
  await exited
  assert.deepEqual((await run('discover', '--all')).map((entry: any) => entry.session), ['T-terminal-one'])
})

test('editor bridge sends rules once before annotations or message, without prose wrappers', async (t) => {
  const f = fakeAmp(); await plugin(f.amp)
  t.after(() => Promise.all(f.disposers.map((dispose) => dispose())))
  const id = 'T-editor'
  await f.events.get('session.start')!({}, f.context(id))
  const root = await realpath(process.cwd())
  const bridge = process.env.REDIFF_TEST_BRIDGE ?? fileURLToPath(new URL('../../../nix/amp_live.py', import.meta.url))
  const run = async (...args: string[]) => JSON.parse((await promisify(execFile)('python3', ['-B', bridge, ...args])).stdout)
  const found = await run('discover', root)
  assert.equal(found.length, 1)
  assert.equal(found[0].session, id)
  assert.ok(found[0].connection.includes('/.cache/rediff/amp/'))
  assert.equal(f.messages.size, 0, 'discovery must not send')
  const payload = join(home, 'message.json')
  const instruction = 'Stage and commit the changes, push, and open a PR.'
  await writeFile(payload, JSON.stringify({ submission_id: 'message-one', repository: root, message: instruction }))
  const ack = await run('send', found[0].connection, id, payload)
  assert.equal(ack.status, 'accepted')
  assert.deepEqual(await run('send', found[0].connection, id, payload), ack)
  assert.equal(f.messages.get(id)!.length, 1, 'retry must not deliver twice')
  const [message, options] = f.messages.get(id)![0]
  assert.equal(options.steer, true)
  const general = JSON.parse(message.content)
  assert.deepEqual(Object.keys(general), ['rules', 'repository', 'message'])
  assert.equal(general.message, instruction)
  assert.match(general.rules[0], /in this feedback or earlier messages in this conversation; do not ask again/)
  assert.match(general.rules[0], /A request to commit includes staging the relevant changes unless the user limits it to already-staged changes/)
  assert.match(general.rules[0], /only when not already authorized/)
  assert.match(general.rules[0], /does not extend to unrelated changes, other repositories, force-pushes, or additional Git\/PR actions/)

  const review = join(home, 'review.json')
  await writeFile(review, JSON.stringify({
    submission_id: 'review-one', repository: root,
    comments: [
      { file: 'demo.lua', side: 'new', line: 42, line_end: 42, text: 'Check this boundary', snapshot_id: 's',
        selection: { kind: 'character', spans: [{ line: 42, start_byte: 3, end_byte: 12 }], text: ['cache[key]'] } },
      { file: 'demo.lua', side: 'new', line: 51, line_end: 53, text: instruction, snapshot_id: 's' },
    ],
    snapshots: { s: { group: 'unstaged', old: 'UNRELATED OLD CONTENT'.repeat(100000), new: 'UNRELATED NEW CONTENT'.repeat(100000) } },
    snapshot_status: { s: 'current' },
  }))
  await run('send', found[0].connection, id, review)
  const feedback = f.messages.get(id)!.at(-1)![0].content
  const batch = JSON.parse(feedback)
  assert.deepEqual(Object.keys(batch), ['rules', 'repository', 'snapshot_archive', 'annotations'])
  assert.equal((feedback.match(/"rules":/g) ?? []).length, 1)
  assert.equal(batch.rules[0], general.rules[0])
  assert.deepEqual(batch.annotations, [
    { file: `${root}/demo.lua`, side: 'new', line: 42, line_end: 42, snapshot_id: 's',
      comparison: 'unstaged', snapshot_status: 'current', text: 'Check this boundary',
      selection: { kind: 'character', spans: [{ line: 42, start_byte: 3, end_byte: 12 }] }, selected_text: 'cache[key]' },
    { file: `${root}/demo.lua`, side: 'new', line: 51, line_end: 53, snapshot_id: 's',
      comparison: 'unstaged', snapshot_status: 'current', text: instruction },
  ])
  assert.ok(!feedback.includes('UNRELATED'))
  assert.ok(feedback.length < 2048, 'Amp receives annotations, not the local snapshot archive')
})
