"""Entry point: `python -m hitl_server` serves MCP over stdio."""

from __future__ import annotations

from .server import build_server


def main() -> None:
    build_server().run("stdio")


if __name__ == "__main__":
    main()
