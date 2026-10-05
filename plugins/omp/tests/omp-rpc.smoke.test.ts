// Smoke test against a real omp binary in RPC mode, with an isolated HOME, no credentials, a scrubbed
// environment so no provider API key can start a model turn, and (on macOS) a sandbox that denies every
// non-loopback network connection. Skipped unless `omp` (or $REDIFF_OMP_BIN) is runnable.
import assert from 'node:assert/strict'
import { execFile, spawn, spawnSync } from 'node:child_process'
import { mkdtempSync, rmSync } from 'node:fs'
import { mkdir, mkdtemp, readFile, readdir, realpath, rm, writeFile } from 'node:fs/promises'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { test } from 'node:test'
import { fileURLToPath } from 'node:url'
import { promisify } from 'node:util'

const bin = process.env.REDIFF_OMP_BIN ?? 'omp'
const bridge = process.env.REDIFF_TEST_BRIDGE ?? fileURLToPath(new URL('../../../nix/amp_live.py', import.meta.url))
const exec = promisify(execFile)
const baseEnv = { PATH: process.env.PATH ?? '', TERM: 'dumb', NO_COLOR: '1' }
// Bun writes caches under HOME even for --version, so probe with a throwaway HOME.
const probeHome = mkdtempSync(join(tmpdir(), 'rediff-omp-version-'))
const version = spawnSync(bin, ['--version'], { encoding: 'utf8', env: { ...baseEnv, HOME: probeHome } })
rmSync(probeHome, { recursive: true, force: true })
const extension = fileURLToPath(new URL('../rediff.ts', import.meta.url))
const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
// Loopback stays reachable for the extension's listener; everything else is refused.
const sandbox = process.platform === 'darwin' && spawnSync('/usr/bin/sandbox-exec', ['-n', 'no-network', '/usr/bin/true']).status === 0
  ? ['/usr/bin/sandbox-exec', '-p',
    '(version 1)(allow default)(deny network-outbound)(allow network-outbound (remote ip "localhost:*"))' +
    '(allow network-outbound (remote unix-socket))']
  : []

test('a real omp RPC session registers, reports activity, follows session switches in-process, and cleans up', {
  skip: version.status === 0 ? false : `${bin} is not available`, timeout: 120_000,
}, async (t) => {
  assert.match(version.stdout.trim(), /(^|\D)18\.4\.4$/, 'this extension is pinned to omp 18.4.4')
  const base = await realpath(await mkdtemp(join(tmpdir(), 'rediff-omp-smoke-')))
  t.after(() => rm(base, { recursive: true, force: true }))
  const home = join(base, 'home')
  const workspace = join(base, 'work space')
  await mkdir(home)
  await mkdir(workspace)
  const registry = join(home, '.cache/rediff/omp')
  // omp's RPC mode refuses to start without a model. Declare one on a closed loopback port whose API key
  // command prints nothing, so the model exists but has no credential and every prompt is refused locally.
  await mkdir(join(home, 'agent'))
  await writeFile(join(home, 'agent/models.yml'), [
    'providers:',
    '  rediff-smoke:',
    '    baseUrl: http://127.0.0.1:9/v1',
    '    apiKey: "!true"',
    '    api: openai-completions',
    '    models:',
    '      - id: offline',
    '        name: Offline smoke model',
    '',
  ].join('\n'))
  const argv = [bin, '--mode', 'rpc', '--no-session', '--no-extensions', '--no-skills', '--no-rules', '--no-lsp',
    '--no-title', '--model', 'rediff-smoke/offline', '-e', extension]
  const command = [...sandbox, ...argv]
  const child = spawn(command[0], command.slice(1), {
    cwd: workspace,
    env: { ...baseEnv, HOME: home, PI_CODING_AGENT_DIR: join(home, 'agent') },
    stdio: ['pipe', 'pipe', 'pipe'],
  })
  const exited = new Promise<number | null>((resolve) => child.once('exit', resolve))
  t.after(() => { if (child.exitCode === null) child.kill('SIGKILL') })
  let stderr = ''
  child.stderr.setEncoding('utf8').on('data', (chunk: string) => { stderr += chunk })
  const records: any[] = []
  let partial = ''
  child.stdout.setEncoding('utf8').on('data', (chunk: string) => {
    partial += chunk
    let newline
    while ((newline = partial.indexOf('\n')) >= 0) {
      const line = partial.slice(0, newline)
      partial = partial.slice(newline + 1)
      if (line.trim()) records.push(JSON.parse(line))
    }
  })
  const until = async <T>(what: string, probe: () => Promise<T | undefined> | T | undefined): Promise<T> => {
    for (let i = 0; i < 600; i++) {
      const value = await probe()
      if (value !== undefined) return value
      if (child.exitCode !== null) break
      await sleep(50)
    }
    throw new Error(`timed out waiting for ${what}; stderr:\n${stderr}\nrecords:\n${JSON.stringify(records).slice(-4000)}`)
  }
  let nextID = 0
  const rpc = async (request: Record<string, unknown>) => {
    const id = `smoke-${++nextID}`
    child.stdin.write(JSON.stringify({ id, ...request }) + '\n')
    const response = await until(`${request.type} response`, () =>
      records.find((record) => record.type === 'response' && record.id === id))
    assert.equal(response.success, true, JSON.stringify(response))
    return response.data
  }
  const descriptors = async () => {
    const entries = await readdir(registry).catch(() => [] as string[])
    return Promise.all(entries.map(async (entry) => {
      const path = join(registry, entry, 'connection.json')
      return { path, descriptor: JSON.parse(await readFile(path, 'utf8')) }
    }))
  }
  const single = (exclude?: string) => until('one descriptor', async () => {
    const entries = (await descriptors().catch(() => [])).filter((entry) => entry.path !== exclude)
    return entries.length === 1 ? entries[0] : undefined
  })
  const sendErrors = () => records.filter((record) =>
    record.type === 'response' && record.command === 'extension_send_user' && record.success === false)
  const openEvents = async (descriptor: any) => {
    const response = await fetch(descriptor.url.replace('/feedback', '/events'),
      { headers: { authorization: `Bearer ${descriptor.token}` } })
    assert.equal(response.status, 200)
    const reader = response.body!.getReader()
    const decoder = new TextDecoder()
    let buffered = ''
    return {
      reader,
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
    }
  }

  t.diagnostic(sandbox.length ? 'non-loopback network denied by sandbox-exec' : 'no network sandbox available')
  const first = await single()
  const state = await rpc({ type: 'get_state' })
  assert.equal(first.descriptor.provider, 'omp')
  assert.equal(first.descriptor.version, 1)
  assert.equal(first.descriptor.root, workspace)
  assert.equal(first.descriptor.thread, state.sessionId, 'the descriptor names the live omp session id')
  assert.ok(state.sessionId.length > 0)
  const bridgeCall = async (...args: string[]) => JSON.parse((await exec('python3', [bridge, ...args], {
    env: { ...baseEnv, HOME: home },
  })).stdout)
  const discovered = await bridgeCall('--provider', 'all', 'discover', '--all')
  assert.equal(discovered.length, 1)
  assert.equal(discovered[0].provider, 'omp')
  assert.equal(discovered[0].session, state.sessionId)
  assert.equal(discovered[0].connection, first.path)
  assert.ok(!('token' in discovered[0]))
  const available = (await rpc({ type: 'get_available_commands' })).commands
    .map((entry: { name: string }) => entry.name.replace(/^\//, ''))
  assert.ok(available.includes('rediff-connect') && available.includes('rediff-disconnect'), available.join(','))

  const headers = { authorization: `Bearer ${first.descriptor.token}` }
  assert.equal((await fetch(first.descriptor.url)).status, 401)
  const probe = await fetch(first.descriptor.url, { headers })
  assert.deepEqual(await probe.json(), { version: 1, root: workspace, thread: state.sessionId })
  const events = await openEvents(first.descriptor)
  const initial = await events.next()
  assert.deepEqual(initial, {
    version: 1, root: workspace, thread: state.sessionId, sequence: 0,
    state: 'idle', title: '', tool: null, files_revision: 0,
  })
  await rpc({ type: 'set_session_name', name: 'Smoke review' })
  assert.equal((await events.next()).title, 'Smoke review', 'SessionManager title changes reach the stream')

  // The extension's command runs through the real slash-command path without a model.
  child.stdin.write(JSON.stringify({ id: 'connect-command', type: 'prompt', message: '/rediff-connect' }) + '\n')
  const notice = await until('the connect notice', () => records.find((record) =>
    record.type === 'extension_ui_request' && record.method === 'notify' && /harness connect omp/.test(record.message ?? '')))
  assert.ok(notice.message.includes(first.path), 'manual connect reuses the automatic connection')
  assert.ok(!notice.message.includes(first.descriptor.token))

  // Real delivery through pi.sendUserMessage: acknowledged on admission. Without credentials omp refuses
  // the turn before any provider request and reports it asynchronously; the HTTP request never waits.
  const submission = join(base, 'submission.json')
  await writeFile(submission, JSON.stringify({ submission_id: 'smoke-1', repository: workspace, message: 'review note' }))
  const feedback = await bridgeCall('--provider', 'omp', 'send', first.path, state.sessionId, submission)
  assert.equal(feedback.status, 'accepted')
  assert.equal(feedback.harness, 'omp-live')
  const refused = await until('omp to process the feedback', () => sendErrors()[0])
  t.diagnostic(`omp refused the admitted feedback: ${refused.error}`)
  assert.match(refused.error, /No API key/, 'feedback reached the prompt flow, which refused without credentials')
  const retry = await bridgeCall('--provider', 'omp', 'send', first.path, state.sessionId, submission)
  assert.deepEqual(retry, feedback)
  await sleep(300)
  assert.equal(sendErrors().length, 1, 'a retried id is not sent to omp again')
  assert.ok(!records.some((record) => record.type === 'message_start' && record.message?.role === 'assistant'),
    'no model output was produced')

  // /new replaces the session id in the same runtime: the connection rotates, omp keeps running.
  await rpc({ type: 'new_session' })
  assert.equal((await events.reader.read().catch(() => ({ done: true }))).done, true, 'the old stream ends')
  const second = await single(first.path)
  const newState = await rpc({ type: 'get_state' })
  assert.notEqual(newState.sessionId, state.sessionId)
  assert.equal(second.descriptor.thread, newState.sessionId)
  assert.notEqual(second.descriptor.token, first.descriptor.token)
  assert.equal(await fetch(first.descriptor.url, { headers }).catch(() => null), null, 'the old listener is closed')
  assert.equal(child.exitCode, null, 'a session switch does not shut the runtime down')
  const after = await fetch(second.descriptor.url, {
    method: 'POST', headers: { authorization: `Bearer ${second.descriptor.token}` },
    body: JSON.stringify({ id: 'smoke-1', content: 'review note' }),
  })
  assert.equal(after.status, 204, 'the replacement connection has its own id cache')
  await until('the second refusal', () => sendErrors()[1])

  child.stdin.end()
  await until('omp to exit', () => child.exitCode ?? undefined).catch(() => child.kill('SIGTERM'))
  await exited
  assert.deepEqual(await descriptors(), [], 'quitting omp removes its descriptor')
})
