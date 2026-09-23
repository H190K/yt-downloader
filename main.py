"""H190K Downloader entry point.

No arguments -> GUI. Any CLI flag (e.g. --update, --check, --setup, --download URL) -> command-line mode.
"""
import sys


def main() -> int:
    if len(sys.argv) > 1:
        from core.cli import run_cli
        return run_cli(sys.argv[1:])
    from ui.app import run_gui
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
