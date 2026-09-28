"""brain — command line entry point.

  brain scan                 show how the projects folder is classified
  brain digest [--week W]    build the no-AI weekly digest (W = 2026-W39, "last", or "this")
"""

from __future__ import annotations

import argparse
import sys
import time

from brain import config as config_mod
from brain import digest as digest_mod
from brain.projects import scan


def cmd_scan(cfg: config_mod.Config, _args: argparse.Namespace) -> int:
    decisions = cfg.load_decisions()
    catalog = scan(cfg.projects_root, decisions.get("folders", {}))
    grouped: dict[str, list[str]] = {}
    for p in catalog.projects.values():
        label = p.name + (" (loose)" if p.kind == "loose" else "")
        label += "".join(f" + {a.name}" for a in p.aliases)
        grouped.setdefault(p.group or "", []).append(label)
    for group in sorted(grouped):
        print(f"\n{group or 'Top level'}")
        for label in sorted(grouped[group], key=str.lower):
            print(f"  {label}")
    review = {p.id: p.needs_review for p in catalog.projects.values()
              if p.needs_review and p.id not in decisions.get("folders", {})}
    if review:
        print("\nFolders to confirm:")
        for fid, why in review.items():
            print(f"  {fid}: {why}")
    return 0


def cmd_digest(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    week = digest_mod.resolve_week(args.week)
    started = time.time()
    digest = digest_mod.build(cfg, week)
    json_path, md_path = digest_mod.write(cfg, digest)
    print(f"{week}: {len(digest.projects)} projects with activity, built in {time.time() - started:.0f}s")
    for p in digest.projects[:15]:
        convs = ", ".join(f"{t} {n}" for t, n in p.sessions.items()) or "no conversations"
        print(f"  {p.attention:5.1f}  {p.id}  ({len(p.active_days)} days; {convs}; "
              f"{p.files_changed} files; {p.commits} commits)")
    print(f"Wrote {md_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="brain")
    parser.add_argument("--vault", help="vault folder (default: $BRAIN_VAULT or the current folder)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="show how the projects folder is classified")
    p_digest = sub.add_parser("digest", help="build the weekly digest without AI")
    p_digest.add_argument("--week", default="last")
    args = parser.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    cfg = config_mod.load(config_mod.find_vault(args.vault))
    return {"scan": cmd_scan, "digest": cmd_digest}[args.command](cfg, args)
