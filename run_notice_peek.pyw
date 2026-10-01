"""Window-only entry point for both Python and the packaged EXE."""

from notice_peek.ui import launch


if __name__ == "__main__":
    raise SystemExit(launch())
