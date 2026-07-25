# bower-bird-mcp

A thin MCP stdio server exposing bower-bird's Python retrieval verbs to any
MCP client — Claude Code in other repos, Codex, etc.

**Shim only.** This package spawns `uv run bb <verb> --json` in the
bower-bird repo, parses stdout as JSON, and returns it as the tool result.
No ranking, no markdown parsing, no graph logic lives here — all of that is
in `src/bower_bird/recall.py`. Read-only: it never writes to the vault.

## Tools

- **`ask`** — `{ query: string, limit?: number }`. Ranked claim recall over
  the knowledge graph (`bb ask "<query>" --limit <n> --json`).

<!-- ponytail: read/links tools land once `bb read` / `bb links` exist in
     Python (retrieval plan step 1 built `ask` only). -->

## Repo root resolution

The server needs to know where the bower-bird repo lives, so it can run
`uv run bb ask` with the right `cwd`. Resolution order:

1. `BOWER_BIRD_REPO` env var, if set.
2. Falls back to two directories up from this package's own install
   location (i.e. this package's parent repo, for local/monorepo use).

Set `BOWER_BIRD_REPO` explicitly when this package is installed standalone
(e.g. via npm) rather than living inside the bower-bird repo.

## Development

```sh
npm install
npm run build   # tsc -> dist/
npm run dev     # tsc --watch
```

## Usage with an MCP client

Point any stdio-based MCP client at `node dist/index.js` (after `npm run
build`), for example in a Claude Code MCP config:

```json
{
  "mcpServers": {
    "bower-bird": {
      "command": "node",
      "args": ["/absolute/path/to/bower-bird/mcp/dist/index.js"],
      "env": { "BOWER_BIRD_REPO": "/absolute/path/to/bower-bird" }
    }
  }
}
```
