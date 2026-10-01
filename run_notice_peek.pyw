"""Development launcher without a console; EXE packaging follows in stage 6."""

from notice_peek.ui import launch


if __name__ == "__main__":
    raise SystemExit(launch())
