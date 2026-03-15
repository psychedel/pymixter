"""Allow running: uv run python -m pymixter.mcp [--project project.json]"""

from pymixter.mcp.server import run_stdio

run_stdio()
