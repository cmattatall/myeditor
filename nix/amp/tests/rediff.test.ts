import assert from 'node:assert/strict'
import { execFile } from 'node:child_process'
import { access, chmod, mkdtemp, readFile, readdir, realpath, rm, stat, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { after, before, test } from 'node:test'
import { fileURLToPath, pathToFileURL } from 'node:url'
import { promisify } from 'node:util'

import plugin from '../rediff.ts'

type Command = (ctx: any) => Promise<void>

let home: string
const originalHome = process.env.HOME
before(async () => {
  home = await mkdtemp(join(tmpdir(), 'rediff-plugin-'))
  process.env.HOME = home
})
after(async () => {
  if (originalHome === undefined) delete process.env.HOME
  else process.env.HOME = originalHome
  await rm(home, { recursive: true, force: true })
})

function fakeAmp(root = process.cwd()) {
  const commands = new Map<string, Command>()
  const events = new Map<string, (event: unknown, ctx: any) => Promise<void>>()
  const disposers: Array<() => Promise<void>> = []
  const messages = new Map<string, any[]>()
  const notifications: string[] = []
  const amp: any = {
    helpers: { filePathFromURI: fileURLToPath },
    logger: { log() {} },
    on(event: string, handler: (event: unknown, ctx: any) => Promise<void>) { events.set(event, handler); return { unsubscribe() {} } },
    registerCommand(id: string, _options: unknown, handler: Command) { commands.set(id, handler); return { unsubscribe() {} } },
    onDispose(handler: () => Promise<void>) { disposers.push(handler); return { unsubscribe() {} } },
  }
  const context = (id: string, append?: (...args: any[]) => Promise<void>) => ({
    thread: { id, title: { get: async (): Promise<string | null> => null }, appendUserMessage: async (...args: any[]) => {
      (messages.get(id) ?? messages.set(id, []).get(id)!).push(args)
      if (args[1]?.steer) await append?.(...args)
    } },
    system: { workspaceRoot: pathToFileURL(root) },
    ui: { notify: async (value: string) => { notifications.push(value) } },
  })
  return { amp, commands, events, disposers, messages, notifications, context }
}

async function connect(f: ReturnType<typeof fakeAmp>, id: string, append?: (...args: any[]) => Promise<void>) {
  await f.commands.get('rediff-connect')!(f.context(id, append))
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

test('connect posts editor instructions to its thread, reuses connections, and cleans up', async () => {
  const f = fakeAmp(); await plugin(f.amp)
  assert.deepEqual([...f.commands.keys()], ['rediff-connect', 'rediff-disconnect'])
  await Promise.all([
    f.commands.get('rediff-connect')!(f.context('T-a')),
    f.commands.get('rediff-connect')!(f.context('T-a')),
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
  assert.deepEqual(Object.keys(a.descriptor), ['version', 'url', 'token', 'root', 'thread'])
  assert.equal(a.descriptor.thread, 'T-a'); assert.equal((await stat(a.path)).mode & 0o777, 0o600)
  const again = await connect(f, 'T-a'); assert.equal(again.path, a.path)
  assert.equal(f.messages.get('T-a')!.at(-1)![0].content, announcement.content)
  assert.ok(!announcement.content.includes(a.descriptor.token), 'never publish the authentication token')
  assert.ok(!f.messages.get('T-b')![0][0].content.includes(a.path), 'commands stay in their own threads')
  assert.deepEqual(f.notifications, [], 'repeated connect also avoids the popup')
  await f.commands.get('rediff-disconnect')!(f.context('T-a'))
  await assert.rejects(access(a.path)); assert.equal((await fetch(a.descriptor.url).catch(() => null)), null)
  await Promise.all(f.disposers.map((dispose) => dispose()))
  await assert.rejects(access(b.path))
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

  await f.commands.get('rediff-disconnect')!(ctx)
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

test('editor bridge sends rules once before annotations or message, without prose wrappers', async (t) => {
  const f = fakeAmp(); await plugin(f.amp)
  t.after(() => Promise.all(f.disposers.map((dispose) => dispose())))
  const id = 'T-editor'
  await f.events.get('session.start')!({}, f.context(id))
  const root = await realpath(process.cwd())
  const bridge = process.env.REDIFF_TEST_BRIDGE ?? fileURLToPath(new URL('../../amp_live.py', import.meta.url))
  const run = async (...args: string[]) => JSON.parse((await promisify(execFile)('python3', ['-B', bridge, ...args])).stdout)
  const found = await run('discover', root)
  assert.equal(found.length, 1)
  assert.equal(found[0].session, id)
  assert.ok(found[0].connection.includes('/.cache/rediff/amp/'))
  assert.equal(f.messages.size, 0, 'discovery must not send')
  const payload = join(home, 'message.json')
  const instruction = 'Commit the changes, push, and open a PR without asking again.'
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
  assert.match(general.rules[0], /unless the user explicitly authorizes that action/)

  const review = join(home, 'review.json')
  await writeFile(review, JSON.stringify({
    submission_id: 'review-one', repository: root,
    comments: [
      { file: 'demo.lua', side: 'new', line: 42, line_end: 42, text: 'Check this boundary', snapshot_id: 's' },
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
  assert.match(batch.rules[0], /unless the user explicitly authorizes that action/)
  assert.deepEqual(batch.annotations, [
    { file: `${root}/demo.lua`, side: 'new', line: 42, line_end: 42, snapshot_id: 's',
      comparison: 'unstaged', snapshot_status: 'current', text: 'Check this boundary' },
    { file: `${root}/demo.lua`, side: 'new', line: 51, line_end: 53, snapshot_id: 's',
      comparison: 'unstaged', snapshot_status: 'current', text: instruction },
  ])
  assert.ok(!feedback.includes('UNRELATED'))
  assert.ok(feedback.length < 2048, 'Amp receives annotations, not the local snapshot archive')
})
