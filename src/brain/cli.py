"""brain — command line entry point.

  sbrain scan                 show how the projects folder is classified
  sbrain digest [--week W]    build the no-AI weekly digest (W = 2026-W39, "last", or "this")
  sbrain run                  update the brain: catch up finished weeks (add --current for this week)
  sbrain run --learn          read logged weeks again for skills and what the owner stated
  sbrain corpus               collect every message the owner typed, for the writing-style study
  sbrain voice <step>         the writing-style study: mark, combine, write, test, status
  sbrain dashboard            open the dashboard
  sbrain install              weekly scheduled run + desktop shortcut (Windows)
  sbrain uninstall            remove both
"""

from __future__ import annotations

import argparse
import json
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
        results = run(cfg, weeks, current=args.current, model_name=args.model, backfill=args.backfill,
                      cli=args.cli, effort=args.effort, learn=args.learn)
    except Busy as exc:
        print(exc)
        return 1
    if cfg.voice.get("weekly_test") and not (args.learn or args.backfill or args.week):
        # The weekly blind test of the writing-style profile; its failure never fails the update.
        try:
            from brain import voice

            voice.weekly(cfg, args)
        except Exception as exc:  # noqa: BLE001
            print(f"voice weekly: skipped after an error: {exc}")
    for r in results:
        if r.get("skipped"):
            continue
        usage = r.get("usage") or {}
        tokens = (f", {usage.get('input_tokens', 0):,} input / {usage.get('output_tokens', 0):,} output tokens"
                  if usage.get("measured", True) else "")
        print(f"{r['week']}: {r['status']}, {r.get('model_calls', 0)} model calls, "
              f"{r.get('new_observations', 0)} new observations, {r['seconds']}s{tokens}")
    return 0


def cmd_corpus(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from pathlib import Path

    from brain import corpus

    exports = [Path(p).expanduser() for p in (args.exports or cfg.voice.get("exports", []))]
    missing = [str(p) for p in exports if not p.is_dir()]
    if missing:
        print(f"Export folder not found: {', '.join(missing)}")
        return 1
    started = time.time()
    collected = corpus.collect(cfg, exports)
    path = corpus.write(cfg, collected)
    summary = json.loads((path.parent / "corpus-summary.json").read_text(encoding="utf-8"))
    print(f"{summary['messages']} unique messages in {time.time() - started:.0f}s, "
          f"{summary['first'][:10] if summary['first'] else '-'} to {summary['last'][:10] if summary['last'] else '-'}")
    for source, n in sorted(summary["by_source"].items()):
        print(f"  {source}: {n} messages from {summary['sessions'].get(source, 0)} sessions")
    for reason, n in summary["excluded"].items():
        print(f"  left out: {n} ({reason})")
    if summary.get("local_messages_with_web_pastes_removed"):
        print(f"  pasted from web chats: removed from {summary['local_messages_with_web_pastes_removed']} coding-agent messages")
    for name in summary["unrecognized_export_files"]:
        print(f"  not a known export format: {name}")
    print(f"Wrote {path}")
    return 0


def cmd_voice(cfg: config_mod.Config, args: argparse.Namespace) -> int:
    from brain import voice

    return voice.main(cfg, args)


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
    p_run.add_argument("--learn", action="store_true",
                       help="read already-logged weeks again for skills, communication, and what the owner "
                            "stated, without rewriting their logs (resumes where it stopped)")
    p_run.add_argument("--cli", choices=("agy", "codex"), help="which CLI answers (default: [model] cli in config.toml, else agy)")
    p_run.add_argument("--model", help="model id for this run (default: [model] name, else newest Gemini Flash "
                                       "or the Codex default); manual-<name> to answer by hand")
    p_run.add_argument("--effort", help="reasoning effort, e.g. low, medium, high (default: [model] effort)")
    p_corpus = sub.add_parser("corpus", help="collect every message the owner typed (no AI)")
    p_corpus.add_argument("--exports", action="append",
                          help="folder of web chat exports (repeatable; default: [voice] exports in config.toml)")
    p_voice = sub.add_parser("voice", help="the writing-style study, one step at a time")
    p_voice.add_argument("step", choices=("mark", "combine", "write", "test", "weekly", "newtest", "status"))
    p_voice.add_argument("--cli", choices=("agy", "codex"), help="CLI for this step (default: [voice] in config.toml)")
    p_voice.add_argument("--model", help="model id for this step")
    p_voice.add_argument("--effort", help="reasoning effort for this step")
    p_voice.add_argument("--workers", type=int, help="model calls in parallel while marking (default 3)")
    p_voice.add_argument("--limit", type=int, help="mark at most this many batches, then stop")
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
    commands = {"scan": cmd_scan, "digest": cmd_digest, "run": cmd_run, "corpus": cmd_corpus, "voice": cmd_voice,
                "dashboard": cmd_dashboard,
                "install": cmd_install, "uninstall": cmd_install}
    return commands[args.command](cfg, args)
