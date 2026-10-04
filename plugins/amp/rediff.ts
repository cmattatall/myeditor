/*
 * Derived from cmattatall/revdiff's Amp plugin; maintained here as rediff.
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
import { createServer, type Server } from 'node:http'
import { homedir } from 'node:os'
import { join } from 'node:path'

export const description = 'Rediff: receive editor review feedback in the current Amp thread.'

const MAX_BODY = 1024 * 1024
const MAX_ID = 256

type Connection = { server: Server; directory: string; descriptor: string }
type Outcome = { content: string; result: Promise<void> }

async function showConnection(thread: PluginThread, descriptor: string): Promise<void> {
  await thread.appendUserMessage({
    type: 'user-message',
    content: [
      'Rediff is connected. Setup information for the human user, not a request for the agent to run commands or edit files.',
      'In rediff, open this same checkout and run :harness connect amp.',
      `If multiple sessions match, select thread ${thread.id} in the picker.`,
      'Use :harness send to compose a message, then :w to send it.',
      '',
      `Connection: ${descriptor}`,
      'This connection is valid until the plugin disconnects or reloads. Run rediff: connect again to show setup information.',
    ].join('\n'),
  })
}

async function closeServer(server: Server): Promise<void> {
  if (!server.listening) return
  server.closeAllConnections()
  await new Promise<void>((resolve) => server.close(() => resolve()))
}

export default async function rediffPlugin(amp: PluginAPI): Promise<void> {
  const connections = new Map<string, Connection>()
  const connecting = new Map<string, Promise<void>>()
  const disconnected = new Set<string>()

  async function disconnect(threadID: string): Promise<boolean> {
    const connection = connections.get(threadID)
    if (!connection) return false
    connections.delete(threadID)
    await rm(connection.directory, { recursive: true, force: true })
    await closeServer(connection.server)
    return true
  }

  async function connect(ctx: PluginCommandContext, announce: boolean): Promise<void> {
    if (!ctx.thread) {
      await ctx.ui.notify('Start a thread before connecting rediff.')
      return
    }
    const existing = connections.get(ctx.thread.id)
    if (existing) {
      if (announce) await showConnection(ctx.thread, existing.descriptor)
      return
    }
    const workspaceURI = ctx.system.workspaceRoot
    if (!workspaceURI) {
      await ctx.ui.notify('Open a workspace before connecting rediff.')
      return
    }

    const root = await realpath(amp.helpers.filePathFromURI(workspaceURI))
    const token = randomBytes(32).toString('hex')
    const outcomes = new Map<string, Outcome>()
    const thread: PluginThread = ctx.thread
    // A title is optional metadata; failure to read it must not prevent review.
    const title = await thread.title.get().catch(() => null)

    const server = createServer((request, response) => {
      void (async () => {
        if (!['GET', 'POST'].includes(request.method ?? '') || request.url !== '/feedback') {
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
          amp.logger.log('rediff feedback append failed; id remains cached', id, error)
          response.writeHead(500).end('feedback outcome is uncertain; check the thread before reconnecting and resending')
        }
      })().catch((error) => {
        amp.logger.log('rediff request failed', error)
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
    })
    const address = server.address()
    if (!address || typeof address === 'string') throw new Error('rediff server has no TCP address')
    const directory = await (async () => {
      const registry = join(homedir(), '.cache/rediff/amp')
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
    try {
      await writeFile(descriptor, JSON.stringify({
        version: 1,
        url: `http://127.0.0.1:${address.port}/feedback`,
        token,
        root,
        thread: thread.id,
        ...(title ? { title } : {}),
      }), { mode: 0o600 })
      connections.set(thread.id, { server, directory, descriptor })
    } catch (error) {
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
      amp.logger.log('rediff automatic connection failed', error)
    }
  }
  amp.on('session.start', autoConnect)
  // Also registers after reloading the plugin in an already-open session.
  amp.on('agent.start', autoConnect)
  amp.registerCommand('rediff-connect', {
    category: 'rediff', title: 'connect', description: 'Connect editor review feedback to this thread',
  }, async (ctx) => {
    if (ctx.thread) disconnected.delete(ctx.thread.id)
    await ensureConnection(ctx, true)
  })
  amp.registerCommand('rediff-disconnect', {
    category: 'rediff', title: 'disconnect', description: 'Disconnect editor review feedback from this thread',
  }, async (ctx) => {
    if (!ctx.thread) return void await ctx.ui.notify('No current thread to disconnect.')
    disconnected.add(ctx.thread.id)
    await connecting.get(ctx.thread.id)
    const removed = await disconnect(ctx.thread.id)
    await ctx.ui.notify(removed ? 'Rediff disconnected.' : 'Rediff is not connected to this thread.')
  })
  amp.onDispose(async () => {
    await Promise.allSettled(connecting.values())
    await Promise.all([...connections.keys()].map(disconnect))
  })
}
