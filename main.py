"""H190K Downloader entry point.

No arguments -> GUI. Any CLI flag (e.g. --update, --check, --setup, --download URL) -> command-line mode.
``--import-tools <dir>`` is used by the installer: handled before ``core.cli`` so it never attaches
to or opens a console window (it logs to data\\import-tools.log instead).
"""
import sys


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--import-tools":
        from core.tool_import import main as import_tools_main
        return import_tools_main(sys.argv[2:])
    if len(sys.argv) > 1:
        from core.cli import run_cli
        return run_cli(sys.argv[1:])
    from ui.app import run_gui
    run_gui()
    return 0


if __name__ == "__main__":
    sys.exit(main())
