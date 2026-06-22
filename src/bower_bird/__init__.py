"""bower-bird: the research-profile capture loop.

A quiet knowledge-collation loop. Send a link to a Telegram bot; the laptop
drains it when awake, routes it down one of two lanes, and writes to the vault:

- bare link        -> to-read   -> append to reading-list.md (metadata only)
- link + a note    -> learned   -> create a clipping + propose [[backlinks]]
  (or `read:` prefix)

Plain Python first. Hermes (the agent framework) comes later; this proves
ingestion end-to-end.
"""

__version__ = "0.1.0"
