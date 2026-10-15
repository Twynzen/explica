"""explica: local, agent-controllable lab for explanatory videos.

Core idea: one Python core (project manifest + declarative scenes + timeline)
served identically through a CLI and an MCP stdio server. Rendering is done by
pycairo (scene frames) piped into FFmpeg, and composition/preview by MLT
(`melt`), which is also what Kdenlive uses, so projects round-trip to native
`.kdenlive` files.
"""

__version__ = "0.1.0"
SCHEMA_PROJECT = "explica/project@1"
SCHEMA_SCENE = "explica/scene@1"
