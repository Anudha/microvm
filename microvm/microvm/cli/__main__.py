"""microvm CLI.

Usage:
    microvm launch --name alice --binary /Applications/Chromium.app/... --url https://example.com
    microvm list
    microvm stop alice
    microvm logs alice
    microvm wipe alice
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

from microvm.instance import Instance
from microvm.platform_utils import state_root


def _instances_root() -> Path:
    return state_root() / "instances"


def _list_existing() -> list[str]:
    root = _instances_root()
    if not root.exists():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def cmd_launch(args: argparse.Namespace) -> int:
    inst = Instance(
        name=args.name,
        binary=args.binary,
        start_url=args.url,
        headless=args.headless,
        extra_args=tuple(args.extra or ()),
        blocklist=set(args.block or ()),
    )
    inst.start()
    print(json.dumps(inst.info(), indent=2))
    if args.wait:
        print(f"[microvm] {inst.name} running (pid={inst.pid}). "
              f"Ctrl-C to stop.", file=sys.stderr)
        try:
            while inst.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            pass
        finally:
            inst.stop()
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    rows = []
    for name in _list_existing():
        root = _instances_root() / name
        rows.append({"name": name, "root": str(root)})
    print(json.dumps(rows, indent=2))
    return 0


def cmd_wipe(args: argparse.Namespace) -> int:
    import shutil
    target = _instances_root() / args.name
    if not target.exists():
        print(f"no such instance: {args.name}", file=sys.stderr)
        return 1
    shutil.rmtree(target)
    print(f"wiped {target}")
    return 0


def cmd_logs(args: argparse.Namespace) -> int:
    target = _instances_root() / args.name
    if not target.exists():
        print(f"no such instance: {args.name}", file=sys.stderr)
        return 1
    for fname in ("fs_journal.jsonl", "net_journal.jsonl"):
        p = target / fname
        if p.exists():
            print(f"--- {fname} ---")
            print(p.read_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="microvm",
        description="Run isolated Chromium instances.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_launch = sub.add_parser("launch", help="Launch an isolated instance.")
    p_launch.add_argument("--name", help="Instance name (default: random).")
    p_launch.add_argument("--binary", help="Path to Chromium-family binary.")
    p_launch.add_argument("--url", default="about:blank", help="Start URL.")
    p_launch.add_argument("--headless", action="store_true")
    p_launch.add_argument("--extra", nargs="*", help="Extra CLI flags to pass through.")
    p_launch.add_argument("--block", nargs="*", help="Hosts to block at the proxy.")
    p_launch.add_argument("--wait", action="store_true",
                          help="Block until the browser exits; then clean up.")
    p_launch.set_defaults(func=cmd_launch)

    p_list = sub.add_parser("list", help="List known instance directories.")
    p_list.set_defaults(func=cmd_list)

    p_wipe = sub.add_parser("wipe", help="Delete an instance's state dir.")
    p_wipe.add_argument("name")
    p_wipe.set_defaults(func=cmd_wipe)

    p_logs = sub.add_parser("logs", help="Show journaled FS/net events.")
    p_logs.add_argument("name")
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
