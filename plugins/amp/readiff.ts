/*
 * Derived from cmattatall/revdiff's Amp plugin; maintained here as readiff.
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
import type { PluginAPI, PluginCommandContext, PluginThread } from '@ampcode/plugin'

import { randomBytes } from 'node:crypto'
import { lstat, mkdir, mkdtemp, realpath, rm, writeFile } from 'node:fs/promises'
import { createServer, type Server, type ServerResponse } from 'node:http'
import { homedir } from 'node:os'
import { isAbsolute, join, relative } from 'node:path'

export const description = 'Readiff: receive editor review feedback in the current Amp thread.'

const MAX_BODY = 1024 * 1024
const MAX_ID = 256

type ActivityState = 'idle' | 'running' | 'awaiting-approval' | 'error' | 'unknown'
type ActivitySnapshot = {
  version: 1; root: string; thread: string; sequence: number
  state: ActivityState; title: string; tool: string | null
  files_revision: number
}
type StreamClient = {
  response: ServerResponse; pending: string | null; heartbeat: NodeJS.Timeout
}
type Activity = {
  call(id: string, tool: string): void
  result(id: string, filesChanged: boolean): void
  subscribe(response: ServerResponse): void
  dispose(): void
}
type Connection = { server: Server; directory: string; descriptor: string; root: string; activity: Activity }
type Outcome = { content: string; result: Promise<void> }

const HEARTBEAT_MS = 15_000
const MAX_STREAM_BUFFER = 64 * 1024

async function createActivity(thread: PluginThread, root: string): Promise<Activity> {
  let state: ActivityState = 'unknown'
  let title = ''
  let observedState = false
  let observedTitle = false
  let sequence = 0
  let filesRevision = 0
  const tools = new Map<string, string>()
  const clients = new Set<StreamClient>()

  const line = (): string => JSON.stringify({
    version: 1, root, thread: thread.id, sequence,
    state: state === 'awaiting-approval' || state === 'error' ? state : tools.size ? 'running' : state,
    title, tool: tools.size ? [...tools.values()].at(-1)! : null,
    files_revision: filesRevision,
  } satisfies ActivitySnapshot) + '\n'
  const write = (client: StreamClient, value: string): void => {
    if (client.response.destroyed || client.response.writableEnded) return
    if (client.pending !== null) {
      if (value === '\n') return // A heartbeat must not overwrite a pending state change.
      client.pending = value
      if (client.response.writableLength > MAX_STREAM_BUFFER) client.response.destroy()
      return
    }
    if (!client.response.write(value)) client.pending = ''
  }
  const publish = (): void => {
    sequence++
    const value = line()
    for (const client of clients) write(client, value)
  }
  const subscriptions = [
    thread.state.subscribe((value) => {
      observedState = true
      state = value
      if (value === 'idle' || value === 'error') tools.clear()
      publish()
    }),
    thread.title.subscribe((value) => { observedTitle = true; title = value ?? ''; publish() }),
  ]
  // Subscribe before reading so an in-flight get cannot overwrite a newer event.
  await Promise.all([
    thread.state.get().then((value) => { if (!observedState) state = value }).catch(() => {}),
    thread.title.get().then((value) => { if (!observedTitle) title = value ?? '' }).catch(() => {}),
  ])
  return {
    call(id, tool) { tools.set(id, tool); publish() },
    result(id, filesChanged) {
      const tracked = tools.delete(id)
      // Retain invalidations in every snapshot, including after stream coalescing/reconnect.
      if (filesChanged) filesRevision++
      if (tracked || filesChanged) publish()
    },
    subscribe(response) {
      const client = { response, pending: null } as StreamClient
      client.heartbeat = setInterval(() => write(client, '\n'), HEARTBEAT_MS)
      client.heartbeat.unref()
      clients.add(client)
      response.on('drain', () => {
        if (client.pending === null) return
        const pending = client.pending
        client.pending = null
        if (pending) write(client, pending)
      })
      const close = () => { clearInterval(client.heartbeat); clients.delete(client) }
      response.once('close', close)
      write(client, line())
    },
    dispose() {
      for (const subscription of subscriptions) subscription.unsubscribe()
      for (const client of clients) {
        clearInterval(client.heartbeat)
        client.response.end()
      }
      clients.clear()
    },
  }
}

async function showConnection(thread: PluginThread, descriptor: string): Promise<void> {
  await thread.appendUserMessage({
    type: 'user-message',
    content: [
      'Readiff is connected. Setup information for the human user, not a request for the agent to run commands or edit files.',
      'In rediff, open this same checkout and run :harness connect amp.',
      `If multiple sessions match, select thread ${thread.id} in the picker.`,
      'Use :harness send to compose a message, then :w to send it.',
      '',
      `Connection: ${descriptor}`,
      'This connection is valid until the plugin disconnects or reloads. Run readiff: connect again to show setup information.',
    ].join('\n'),
  })
}

async function closeServer(server: Server): Promise<void> {
  if (!server.listening) return
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
}

export default async function readiffPlugin(amp: PluginAPI): Promise<void> {
  const connections = new Map<string, Connection>()
  const connecting = new Map<string, Promise<void>>()
  const disconnected = new Set<string>()

  async function disconnect(threadID: string): Promise<boolean> {
    const connection = connections.get(threadID)
    if (!connection) return false
    connections.delete(threadID)
    connection.activity.dispose()
    await rm(connection.directory, { recursive: true, force: true })
    await closeServer(connection.server)
    return true
  }

  async function connect(ctx: PluginCommandContext, announce: boolean): Promise<void> {
    if (!ctx.thread) {
      await ctx.ui.notify('Start a thread before connecting readiff.')
      return
    }
    const existing = connections.get(ctx.thread.id)
    if (existing) {
      if (announce) await showConnection(ctx.thread, existing.descriptor)
      return
    }
    const workspaceURI = ctx.system.workspaceRoot
    if (!workspaceURI) {
      await ctx.ui.notify('Open a workspace before connecting readiff.')
      return
    }

    const root = await realpath(amp.helpers.filePathFromURI(workspaceURI))
    const token = randomBytes(32).toString('hex')
    const outcomes = new Map<string, Outcome>()
    const thread: PluginThread = ctx.thread
    // A title is optional metadata; failure to read it must not prevent review.
    const title = await thread.title.get().catch(() => null)
    const activity = await createActivity(thread, root)

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
        if (events) {
          response.writeHead(200, {
            'Content-Type': 'application/x-ndjson',
            'Cache-Control': 'no-cache',
            Connection: 'keep-alive',
          })
          activity.subscribe(response)
          return
        }
        if (request.method === 'GET') {
          response.writeHead(200, { 'Content-Type': 'application/json' })
            .end(JSON.stringify({ version: 1, root, thread: thread.id }))
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
        if (
          typeof body !== 'object' || body === null || Array.isArray(body) ||
          typeof (body as { id?: unknown }).id !== 'string' ||
          (body as { id: string }).id.length === 0 || (body as { id: string }).id.length > MAX_ID ||
          typeof (body as { content?: unknown }).content !== 'string' ||
          (body as { content: string }).content.trim().length === 0
        ) {
          response.writeHead(400).end('expected a bounded id and nonempty content')
          return
        }
        const { id, content } = body as { id: string; content: string }
        const previous = outcomes.get(id)
        if (previous && previous.content !== content) {
          response.writeHead(409).end('id was already used with different content')
          return
        }
        const outcome = previous ?? {
          content,
          result: thread.appendUserMessage(
            { type: 'user-message', content },
            { steer: true },
          ),
        }
        if (!previous) outcomes.set(id, outcome)
        try {
          await outcome.result
          response.writeHead(204).end()
        } catch (error) {
          amp.logger.log('readiff feedback append failed; id remains cached', id, error)
          response.writeHead(500).end('feedback outcome is uncertain; check the thread before reconnecting and resending')
        }
      })().catch((error) => {
        amp.logger.log('readiff request failed', error)
        if (!response.headersSent) response.writeHead(500)
        response.end('internal error')
      })
    })

    await new Promise<void>((resolve, reject) => {
      server.once('error', reject)
      server.listen(0, '127.0.0.1', () => {
        server.off('error', reject)
        resolve()
      })
    }).catch((error) => {
      activity.dispose()
      throw error
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('readiff server has no TCP address')
    const directory = await (async () => {
      const registry = join(homedir(), '.cache/rediff/amp')
      await mkdir(registry, { recursive: true, mode: 0o700 })
      const info = await lstat(registry)
      if (!info.isDirectory() || (info.mode & 0o077) !== 0 || info.uid !== process.getuid?.()) {
        throw new Error('readiff connection registry must be a private directory owned by you')
      }
      return mkdtemp(join(registry, 'session-'))
    })().catch(async (error) => {
      activity.dispose()
      await closeServer(server)
      throw error
    })
    const descriptor = join(directory, 'connection.json')
    try {
      await writeFile(descriptor, JSON.stringify({
        version: 1,
        url: `http://127.0.0.1:${address.port}/feedback`,
        token,
        root,
        thread: thread.id,
        capabilities: ['activity'],
        ...(title ? { title } : {}),
      }), { mode: 0o600 })
      connections.set(thread.id, { server, directory, descriptor, root, activity })
    } catch (error) {
      activity.dispose()
      await closeServer(server)
      await rm(directory, { recursive: true, force: true })
      throw error
    }
    if (announce) await showConnection(thread, descriptor)
  }

  async function ensureConnection(ctx: PluginCommandContext, announce: boolean): Promise<void> {
    if (!ctx.thread) return connect(ctx, announce)
    const id = ctx.thread.id
    const pending = connecting.get(id)
    if (pending) {
      await pending
      const connection = connections.get(id)
      if (announce && connection) await showConnection(ctx.thread, connection.descriptor)
      return
    }
    const task = connect(ctx, announce)
    connecting.set(id, task)
    try { await task } finally { connecting.delete(id) }
  }

  async function autoConnect(_event: unknown, ctx: PluginCommandContext): Promise<void> {
    if (!ctx.thread || !ctx.system.workspaceRoot || disconnected.has(ctx.thread.id)) return
    try {
      await ensureConnection(ctx, false)
    } catch (error) {
      amp.logger.log('readiff automatic connection failed', error)
    }
  }
  amp.on('session.start', autoConnect)
  // Also registers after reloading the plugin in an already-open session.
  amp.on('agent.start', autoConnect)
  amp.on('tool.call', (event) => {
    connections.get(event.thread.id)?.activity.call(event.toolUseID, event.tool)
    return { action: 'allow' }
  })
  amp.on('tool.result', (event) => {
    const connection = connections.get(event.thread.id)
    if (!connection) return
    let filesChanged = false
    try {
      filesChanged = (amp.helpers.filesModifiedByToolCall(event) ?? []).some((uri) => {
        const path = relative(connection.root, amp.helpers.filePathFromURI(uri))
        return path !== '' && path !== '..' && !path.startsWith('../') && !isAbsolute(path)
      })
    } catch (error) {
      amp.logger.log('readiff file-change detection failed', error)
    }
    // Error/cancelled tools may have partially applied edits; Git decides what actually changed.
    connection.activity.result(event.toolUseID, filesChanged)
  })
  amp.registerCommand('readiff-connect', {
    category: 'readiff', title: 'connect', description: 'Connect editor review feedback to this thread',
  }, async (ctx) => {
    if (ctx.thread) disconnected.delete(ctx.thread.id)
    await ensureConnection(ctx, true)
  })
  amp.registerCommand('readiff-disconnect', {
    category: 'readiff', title: 'disconnect', description: 'Disconnect editor review feedback from this thread',
  }, async (ctx) => {
    if (!ctx.thread) return void await ctx.ui.notify('No current thread to disconnect.')
    disconnected.add(ctx.thread.id)
    await connecting.get(ctx.thread.id)
    const removed = await disconnect(ctx.thread.id)
    await ctx.ui.notify(removed ? 'Readiff disconnected.' : 'Readiff is not connected to this thread.')
  })
  amp.onDispose(async () => {
    await Promise.allSettled(connecting.values())
    await Promise.all([...connections.keys()].map(disconnect))
  })
}
