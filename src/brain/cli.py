"""brain — command line entry point.

  sbrain scan                 show how the projects folder is classified
  sbrain digest [--week W]    build the no-AI weekly digest (W = 2026-W39, "last", or "this")
  sbrain run                  update the brain: catch up finished weeks (add --current for this week)
  sbrain dashboard            open the dashboard
  sbrain install              weekly scheduled run + desktop shortcut (Windows)
  sbrain uninstall            remove both
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
    catalog = scan(cfg.projects_root, decisions.get("folders", {}), cfg.places)
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


def cmd_run(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from brain.run import Busy, run

    weeks = [digest_mod.resolve_week(w) for w in args.week] if args.week else None
    try:
        results = run(cfg, weeks, current=args.current, model_name=args.model, backfill=args.backfill)
    except Busy as exc:
        print(exc)
        return 1
    for r in results:
        if r.get("skipped"):
            continue
        print(f"{r['week']}: {r['status']}, {r.get('model_calls', 0)} model calls, "
              f"{r.get('new_observations', 0)} new observations, {r['seconds']}s")
    return 0


def cmd_dashboard(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from brain.dashboard import serve

    return serve(cfg, port=args.port, open_browser=not args.no_browser)


def cmd_install(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from brain.install import install, uninstall

    for line in (uninstall() if args.command == "uninstall" else install(cfg)):
        print(line)
    return 0


def dashboard_main() -> int:
    """Windowless entry for the desktop shortcut: `sbrain-dashboard --vault <path>`."""
    return main([*sys.argv[1:], "dashboard"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sbrain")
    parser.add_argument("--vault", help="vault folder (default: $BRAIN_VAULT or the current folder)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("scan", help="show how the projects folder is classified")
    p_digest = sub.add_parser("digest", help="build the weekly digest without AI")
    p_digest.add_argument("--week", default="last")
    p_run = sub.add_parser("run", help="update the brain")
    p_run.add_argument("--week", action="append", help="a specific week (repeatable); default: catch up")
    p_run.add_argument("--current", action="store_true", help="also write the week in progress")
    p_run.add_argument("--backfill", action="store_true", help="walk all history from the first conversation, oldest first")
    p_run.add_argument("--model", help="model id for this run (default: newest Gemini Flash)")
    p_dash = sub.add_parser("dashboard", help="open the dashboard")
    p_dash.add_argument("--port", type=int, default=8765)
    p_dash.add_argument("--no-browser", action="store_true")
    sub.add_parser("install", help="weekly scheduled run and desktop shortcut (Windows)")
    sub.add_parser("uninstall", help="remove the scheduled run and shortcut")
    args = parser.parse_args(argv)

    cfg = config_mod.load(config_mod.find_vault(args.vault))
    if sys.stdout is None or sys.stderr is None:
        # Windowless (scheduled task or shortcut): keep a log in the vault's working folder.
        cfg.work_dir.mkdir(parents=True, exist_ok=True)
        sys.stdout = sys.stderr = open(cfg.work_dir / "brain.log", "a", encoding="utf-8", buffering=1)
        print(f"--- {time.strftime('%Y-%m-%d %H:%M:%S')} brain {' '.join(argv or sys.argv[1:])}")
    elif hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    commands = {"scan": cmd_scan, "digest": cmd_digest, "run": cmd_run, "dashboard": cmd_dashboard,
                "install": cmd_install, "uninstall": cmd_install}
    return commands[args.command](cfg, args)
