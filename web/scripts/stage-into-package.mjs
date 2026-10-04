/**
 * Copy the built frontend into the Python package so a wheel can carry it.
 *
 * The workbench is served by `iris web` from `iris/web/dist` when IRIS is
 * installed, and from `web/dist` in a source checkout. Building only produces the
 * second one, so a wheel built without this step installs a `iris web` that answers
 * every page with 503 -- the failure mode this script exists to prevent.
 *
 * Run through `npm run build:pkg`, which builds first. The copy replaces the target
 * wholesale: a stale hashed bundle left behind by an earlier build is a file nothing
 * references any more, and it would still be shipped.
 */

import { cp, rm, stat } from 'node:fs/promises'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))
const source = join(here, '..', 'dist')
const target = join(here, '..', '..', 'src', 'iris', 'web', 'dist')

const built = await stat(join(source, 'index.html')).catch(() => null)
if (built === null) {
  console.error(`stage-into-package: ${source}/index.html is missing -- run "npm run build" first.`)
  process.exit(1)
}

await rm(target, { recursive: true, force: true })
await cp(source, target, { recursive: true })

const copied = await stat(join(target, 'index.html'))
if (!copied.isFile()) {
  console.error(`stage-into-package: ${target}/index.html is missing after the copy.`)
  process.exit(1)
}

console.log(`staged ${source} -> ${target}`)