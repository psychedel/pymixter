"""Entry point: launch TUI or CLI."""

import sys


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "cli":
        # CLI mode: `uv run python main.py cli <command> ...`
        sys.argv = sys.argv[1:]  # strip "cli"
        from pymixter.cli.main import main as cli_main
        cli_main()
    else:
        # TUI mode: `uv run python main.py [project.json]`
        project_path = sys.argv[1] if len(sys.argv) > 1 else "project.json"
        from pymixter.tui.app import MixApp
        app = MixApp(project_path=project_path)
        app.run()


if __name__ == "__main__":
    main()
