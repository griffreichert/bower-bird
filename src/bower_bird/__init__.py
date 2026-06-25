"""bower-bird: the research-profile capture loop.

A quiet knowledge-collation loop. Send a link to a Telegram bot; the laptop
pulls it when awake, routes it down one of several lanes, and writes to the vault:

- bare link        -> to-read   -> fetch + render -> readable .md in inbox/
- link + a note    -> learned   -> create a clipping + propose [[backlinks]]
  (or `read:` prefix)
- tool: + link     -> tools     -> append to tools.md shelf (not knowledge)
- walled page      -> to-clip   -> append to to-clip.md (open in browser + Web Clipper)

Plain Python first. Hermes (the agent framework) comes later; this proves
ingestion end-to-end.
"""

__version__ = "0.1.0"
