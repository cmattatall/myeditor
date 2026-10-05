// A separate plugin host, with no shared memory or terminal state with the editor.
import { pathToFileURL } from 'node:url'
import plugin from '../rediff.ts'

const [root, id] = process.argv.slice(2)
const handlers = new Map<string, (event: unknown, context: any) => unknown>()
const disposers: Array<() => Promise<void>> = []
const observable = (value: string) => ({
  get: async () => value,
  subscribe: () => ({ unsubscribe() {} }),
})
await plugin({
  helpers: { filePathFromURI: (uri: URL) => decodeURIComponent(uri.pathname) },
  logger: { log() {} },
  on(event: string, handler: (event: unknown, context: any) => unknown) {
    handlers.set(event, handler)
    return { unsubscribe() {} }
  },
  registerCommand() { return { unsubscribe() {} } },
  onDispose(dispose: () => Promise<void>) {
    disposers.push(dispose)
    return { unsubscribe() {} }
  },
} as any)
await handlers.get('session.start')!({}, {
  system: { workspaceRoot: pathToFileURL(root) },
  thread: {
    id, title: observable(id), state: observable('idle'),
    appendUserMessage: async (message: unknown) => { process.send!({ message }) },
  },
})
process.send!({ ready: true })
process.once('message', async () => {
  await Promise.all(disposers.map(dispose => dispose()))
  process.disconnect()
})
