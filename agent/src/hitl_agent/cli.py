"""Command line entry point.

hitl-agent --server python --auto-reject
hitl-agent --server typescript --approve-all
hitl-agent --server typescript                              # interactive approvals
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shlex
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from .approver import ApproveAllApprover, Approver, AutoRejectApprover, TerminalApprover
from .loop import Tools, render_audit, run_triage
from .planner import make_planner

ROOT = Path(__file__).resolve().parents[3]


def server_command(name: str) -> list[str]:
    if name == "python":
        return [sys.executable, "-m", "hitl_server"]
    if name == "typescript":
        return ["node", str(ROOT / "typescript" / "dist" / "index.js")]
    raise SystemExit(f"unknown server {name}")


async def run(command: list[str], args: argparse.Namespace, approver: Approver) -> int:
    env = dict(os.environ)
    if args.fixed_clock:
        env["HITL_CLOCK"] = f"fixed:{args.fixed_clock}"
    if args.audit_path:
        env["HITL_AUDIT_PATH"] = str(Path(args.audit_path).resolve())
    params = StdioServerParameters(command=command[0], args=command[1:], env=env, cwd=str(ROOT))
    with open(os.devnull, "w") as devnull:
        async with stdio_client(params, errlog=devnull) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            print(f"Connected to {init.serverInfo.name} {init.serverInfo.version} via: {shlex.join(command)}")
            print(f"Planner: {args.planner}. Approvals: {args.mode}.")
            report = await run_triage(Tools(session), make_planner(args.planner), approver, min_severity=args.min_severity, site=args.site)
    render_audit(report)
    return 0 if report.verify.get("valid") else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="hitl-agent", description="Human-in-the-loop triage agent over MCP.")
    target = parser.add_mutually_exclusive_group()
    target.add_argument("--server", choices=["python", "typescript"], default="python")
    target.add_argument("--server-cmd", help="any stdio MCP server command implementing the spec")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--auto-reject", action="store_true", help="reject every pending action (CI)")
    mode.add_argument("--approve-all", action="store_true", help="approve every pending action (demo)")
    parser.add_argument("--reviewer", help="reviewer identity recorded in the audit log")
    parser.add_argument("--planner", choices=["stub", "llm"], default="stub")
    parser.add_argument("--min-severity", type=int, default=3)
    parser.add_argument("--site")
    parser.add_argument("--fixed-clock", metavar="ISO", help="deterministic server clock, e.g. 2026-03-02T09:00:00Z")
    parser.add_argument("--audit-path", help="persist the audit chain as JSON Lines")
    args = parser.parse_args(argv)

    approver: Approver
    if args.auto_reject:
        args.mode = "auto-reject"
        approver = AutoRejectApprover(args.reviewer or "ci.reviewer")
    elif args.approve_all:
        args.mode = "approve-all"
        approver = ApproveAllApprover(args.reviewer or "demo.reviewer")
    else:
        if not sys.stdin.isatty():
            parser.error("stdin is not a terminal; pass --auto-reject or --approve-all")
        args.mode = "interactive"
        approver = TerminalApprover(args.reviewer)

    command = shlex.split(args.server_cmd) if args.server_cmd else server_command(args.server)
    return asyncio.run(run(command, args, approver))


if __name__ == "__main__":
    sys.exit(main())
