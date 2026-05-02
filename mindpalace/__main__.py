"""``python -m mindpalace`` and the ``mindpalace`` console script land here."""

from __future__ import annotations

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(prog="mindpalace")
    sub = parser.add_subparsers(dest="cmd")

    p_serve = sub.add_parser("serve", help="Run the MCP server.")
    p_serve.add_argument(
        "--transport", choices=["stdio", "http"], default="stdio",
        help="MCP transport. Default: stdio (the right answer for local LLM clients).",
    )
    p_serve.add_argument("--host", default="127.0.0.1")
    p_serve.add_argument("--port", type=int, default=7077)

    sub.add_parser("init", help="Create the data dir + DB and run migrations.")
    sub.add_parser("doctor", help="Run health_check + a smoke create/read/delete.")

    args = parser.parse_args()
    if args.cmd in (None, "serve"):
        return _serve(getattr(args, "transport", "stdio"),
                      getattr(args, "host", "127.0.0.1"),
                      getattr(args, "port", 7077))
    if args.cmd == "init":
        return _init()
    if args.cmd == "doctor":
        return _doctor()
    parser.print_help()
    return 1


def _serve(transport: str, host: str, port: int) -> int:
    from mindpalace.server import mcp
    if transport == "stdio":
        mcp.run()
    else:
        mcp.run(transport="http", host=host, port=port)
    return 0


def _init() -> int:
    from mindpalace.db.engine import engine
    from mindpalace.db.models import Base
    Base.metadata.create_all(bind=engine)
    print("MindPalace initialised. DB and data dir ready.")
    return 0


def _doctor() -> int:
    from mindpalace.server import session_scope
    from mindpalace.core.operations import (
        add_container, add_item, delete_container, delete_item, initialize_root,
    )
    from mindpalace.core.resolver import resolve_item_in_path
    with session_scope() as s:
        initialize_root(s)
        add_container(s, [], "_doctor_room")
        add_container(s, ["_doctor_room"], "_doctor_drawer")
        add_item(s, ["_doctor_room", "_doctor_drawer"], "_doctor_item")
        item = resolve_item_in_path(s, ["_doctor_room", "_doctor_drawer"], "_doctor_item")
        assert item.name == "_doctor_item"
        delete_item(s, "_doctor_item")
        delete_container(s, ["_doctor_room"])
    print("OK — round-trip create/read/delete succeeded.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
