"""Entry point: launch TUI or CLI."""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        # CLI mode: `uv run python main.py cli <command> ...`
        sys.argv = sys.argv[1:]  # strip "cli"
        from pymixter.cli.main import main as cli_main
        cli_main()
    elif len(sys.argv) > 1 and sys.argv[1] == "web":
        # Web mode: `uv run python main.py web [--port 8000]`
        port = 8000
        if "--port" in sys.argv:
            idx = sys.argv.index("--port")
            if idx + 1 < len(sys.argv):
                port = int(sys.argv[idx + 1])
        from pathlib import Path
        from textual_serve.server import Server
        host = "localhost"
        if "--host" in sys.argv:
            idx = sys.argv.index("--host")
            if idx + 1 < len(sys.argv):
                host = sys.argv[idx + 1]
        server = Server(
            command="uv run python main.py",
            host=host,
            port=port,
            title="PyMixter DJ Mix Studio",
            templates_path=Path(__file__).parent / "web" / "templates",
        )
        server.serve()
    elif len(sys.argv) > 1 and sys.argv[1] == "mcp":
        # MCP server mode: `uv run python main.py mcp [--project project.json]`
        sys.argv = sys.argv[1:]  # strip "mcp"
        from pymixter.mcp.server import run_stdio
        run_stdio()
    else:
        # TUI mode: `uv run python main.py [project.json]`
        project_path = sys.argv[1] if len(sys.argv) > 1 else "project.json"
        from pymixter.tui.app import MixApp
        app = MixApp(project_path=project_path)
        app.run()


if __name__ == "__main__":
    main()
