"""Run the shared conformance suite against every server implementation.

    python conformance/run.py                       # both servers, all cases
    python conformance/run.py --case 05_policy      # one case
    python conformance/run.py --server python=".venv/bin/python -m hitl_server"

Exit status is non-zero if any server fails an expectation or the servers diverge.
"""

from __future__ import annotations

import argparse
import asyncio
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import Runner, StepOutcome, default_servers, divergences, load_cases, load_spec  # noqa: E402


async def run(servers: dict[str, list[str]], case_names: list[str] | None, verbose: bool) -> int:
    runner = Runner(load_spec())
    cases = load_cases(case_names)
    if not cases:
        print("no conformance cases selected", file=sys.stderr)
        return 2
    failures = 0
    total_steps = 0
    print(f"servers: {', '.join(servers)}  cases: {len(cases)}")
    for case in cases:
        results: dict[str, list[StepOutcome]] = {}
        for name, command in servers.items():
            results[name] = await runner.run_case(command, case)
        total_steps += sum(len(r) for r in results.values())
        case_problems = [f"[{n}] {o.label}: {p}" for n, outs in results.items() for o in outs for p in o.problems]
        case_problems += [f"[diverge] {d}" for d in divergences(results)]
        steps = len(next(iter(results.values())))
        status = "PASS" if not case_problems else "FAIL"
        print(f"  {status}  {case['name']:<22} {steps:>3} steps  {case.get('description', '')}")
        if verbose and not case_problems:
            for outcome in next(iter(results.values())):
                print(f"          - {outcome.label}")
        for problem in case_problems:
            print(f"          {problem}")
        failures += len(case_problems)
    verdict = "conformant and in agreement" if failures == 0 else f"{failures} problem(s)"
    print(f"{total_steps} step executions across {len(servers)} servers: {verdict}")
    return 0 if failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--server", action="append", default=[], metavar="NAME=COMMAND", help="override or add a server")
    parser.add_argument("--only", action="append", default=[], metavar="NAME", help="run only these servers")
    parser.add_argument("--case", action="append", default=None, help="run only these cases")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    servers = default_servers()
    for item in args.server:
        name, _, command = item.partition("=")
        servers[name] = shlex.split(command)
    if args.only:
        servers = {k: v for k, v in servers.items() if k in args.only}
    return asyncio.run(run(servers, args.case, args.verbose))


if __name__ == "__main__":
    sys.exit(main())
