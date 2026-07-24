"""bower-bird: the research-profile capture loop.

A quiet knowledge-collation loop (antilibrary model: everything sent is
shelved immediately). Send to a Telegram bot; the laptop pulls when awake,
routes down one of a few lanes, and writes to the vault:

- any link (+note) -> shelve  -> source node in brain/sources/, body inlined
- pasted prose     -> paste   -> its own source node, sender as author
- tool: + link     -> tools   -> append to tools.md shelf (not knowledge)
- walled page      -> to-clip -> append to to-clip.md (browser + Web Clipper)

Plain Python first. Hermes (the agent framework) comes later; this proves
ingestion end-to-end.
"""

__version__ = "0.1.0"
