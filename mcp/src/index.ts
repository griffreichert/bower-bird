#!/usr/bin/env node
/**
 * bower-bird-mcp — a thin MCP stdio shim over bower-bird's Python retrieval
 * verbs. Its whole job: spawn `uv run bb <verb> --json`, parse stdout as
 * JSON, return it as the tool result. All ranking/graph/parsing logic lives
 * in Python (src/bower_bird/recall.py) — nothing here re-derives it.
 *
 * Read-only: every verb this shim wires up must be read-only in bower-bird
 * (no vault writes).
 */

import { execFile } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { promisify } from "node:util";

import { McpServer } from "@modelcontextprotocol/sdk/server/mcp.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";
import { z } from "zod";

const execFileAsync = promisify(execFile);

// Repo root: BOWER_BIRD_REPO env var, else derived from this package's own
// location (mcp/dist/index.js -> repo root is two directories up).
const packageDir = path.resolve(fileURLToPath(import.meta.url), "../..");
const repoRoot = process.env.BOWER_BIRD_REPO ?? path.resolve(packageDir, "..");

const server = new McpServer({ name: "bower-bird-mcp", version: "0.1.0" });

server.registerTool(
  "ask",
  {
    description:
      "Ranked claim recall over the bower-bird knowledge vault (read-only, no LLM). " +
      "Returns claims with source/concept provenance for a query.",
    inputSchema: {
      query: z.string().describe("Topic or question to search the vault for."),
      limit: z.number().int().positive().optional().describe("Max claims to return (default 20)."),
    },
  },
  async ({ query, limit }) => {
    const args = ["run", "bb", "ask", query, "--json"];
    if (limit !== undefined) {
      args.push("--limit", String(limit));
    }
    try {
      const { stdout } = await execFileAsync("uv", args, { cwd: repoRoot });
      return { content: [{ type: "text", text: stdout }] };
    } catch (err) {
      // err.stderr is "" (not undefined) for spawn-level failures (e.g. bad
      // cwd) — fall through to err.message so the caller isn't left blank.
      const e = err as { stderr?: string; message?: string };
      const stderr = e.stderr || e.message || String(err);
      return { content: [{ type: "text", text: stderr }], isError: true };
    }
  },
);

// ponytail: read/links tools land when `bb read` / `bb links` exist in Python
// (retrieval plan step 1 covers `ask` only; steps for read/links are unbuilt).

const transport = new StdioServerTransport();
await server.connect(transport);
