from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .basic_memory_integration import reindex
from .config import RuntimePaths, dashboard_runtime, load_defaults, load_runtime_config, setup_runtime, vault_root
from .dashboard import build_dashboard, install_dashboard_shortcut, open_dashboard
from .dashboard_server import serve_dashboard
from .gitops import sync_protocol_draft
from .health import report as health_report, write_report
from .installer import install_standalone_codex, login_dedicated_account
from .model_runner import ModelRole, build_evidence_packet, canary, run_model
from .orchestrator import (
    approve_bootstrap,
    bootstrap,
    decide_review,
    decide_review_group,
    incremental,
    refresh_bootstrap_evidence,
    refresh_project,
    scheduled,
)
from .profile import QUESTIONS, answer_interview, create_interview, interview_status
from .publisher import write_bootstrap_review_artifacts, write_review_artifacts
from .review import find_review_group, review_summary
from .service import build_context, recent_activity, search
from .state import StateStore


def _json(value: object) -> None:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    ensure_ascii = encoding not in {"utf8", "utf_8"}
    print(json.dumps(value, indent=2, ensure_ascii=ensure_ascii, default=str))


def _common() -> tuple[RuntimePaths, dict, dict, StateStore]:
    paths = setup_runtime()
    return paths, load_runtime_config(paths), load_defaults(), StateStore(paths.state)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="sb", description="Evidence-backed Personal Second Brain Protocol")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    auth = sub.add_parser("auth")
    auth.add_subparsers(dest="action", required=True).add_parser("login")
    boot = sub.add_parser("bootstrap")
    boot.add_argument("--linkedin-export", "--linkedin-pdf", dest="linkedin_export", type=Path)
    boot.add_argument("--approve", action="store_true")
    boot.add_argument("--refresh-evidence", action="store_true")
    sub.add_parser("daily")
    sub.add_parser("weekly")
    sub.add_parser("scheduled")
    refresh = sub.add_parser("refresh-project")
    refresh.add_argument("project")
    review = sub.add_parser("review")
    review_sub = review.add_subparsers(dest="action")
    review_list = review_sub.add_parser("list")
    review_list.add_argument("--raw", action="store_true")
    review_sub.add_parser("digest")
    review_show = review_sub.add_parser("show")
    review_show.add_argument("id")
    approve = review_sub.add_parser("approve")
    approve.add_argument("id")
    reject = review_sub.add_parser("reject")
    reject.add_argument("id")
    reject.add_argument("--reason", required=True)
    resolve = review_sub.add_parser("resolve")
    resolve.add_argument("id")
    resolve.add_argument("--answer", required=True)
    review_answer = review_sub.add_parser("answer")
    review_answer.add_argument("group")
    review_answer.add_argument("item", type=int)
    review_answer.add_argument("--answer", required=True)
    approve_group = review_sub.add_parser("approve-group")
    approve_group.add_argument("id")
    reject_group = review_sub.add_parser("reject-group")
    reject_group.add_argument("id")
    reject_group.add_argument("--reason", required=True)
    interview = sub.add_parser("interview")
    interview_sub = interview.add_subparsers(dest="action")
    interview_sub.add_parser("status")
    interview_sub.add_parser("next")
    answer = interview_sub.add_parser("answer")
    answer.add_argument("question_id")
    answer.add_argument("answer")
    find = sub.add_parser("search")
    find.add_argument("query")
    find.add_argument("--limit", type=int, default=10)
    writer = sub.add_parser("write-as-me")
    writer.add_argument("request")
    career = sub.add_parser("career")
    career.add_argument("request")
    sub.add_parser("reindex")
    sub.add_parser("health")
    models = sub.add_parser("models")
    models.add_subparsers(dest="action", required=True).add_parser("check")
    protocol = sub.add_parser("protocol")
    protocol_publish = protocol.add_subparsers(dest="action", required=True).add_parser("publish")
    protocol_publish.add_argument("--if-changed", action="store_true")
    dashboard = sub.add_parser("dashboard")
    dashboard_sub = dashboard.add_subparsers(dest="action")
    dashboard_sub.add_parser("build")
    dashboard_sub.add_parser("open")
    dashboard_sub.add_parser("install")
    dashboard_serve = dashboard_sub.add_parser("serve")
    dashboard_serve.add_argument("--no-browser", action="store_true")
    dashboard.set_defaults(action="open")
    return parser


def _draft(kind: str, request: str) -> dict:
    paths, _config, defaults, store = _common()
    context_rows = search(paths, vault_root(), request, limit=20)
    if kind == "write-as-me":
        voice_rows = search(
            paths,
            vault_root(),
            "the user voice writing style tone phrasing natural wording voice samples",
            limit=20,
        )
        seen = {
            json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            for row in context_rows
        }
        for row in voice_rows:
            key = json.dumps(row, sort_keys=True, ensure_ascii=False, default=str)
            if key not in seen:
                context_rows.append(row)
                seen.add(key)
            if len(context_rows) >= 30:
                break
    evidence = []
    for index, row in enumerate(context_rows, 1):
        evidence.append(
            {
                "id": f"ev-canonical-{index}",
                "source_type": "canonical-note",
                "source_ref": f"canonical:{index}",
                "project_id": None,
                "kind": "canonical_context",
                "occurred_at": None,
                "payload": {"request": request, "context": row},
            }
        )
    if not evidence:
        return {"content": "No verified canonical context was found for this request.", "evidence_refs": []}
    run_id = store.start_run(kind, defaults["models"]["weekly"]["name"], "medium")
    try:
        result, receipt = run_model(
            paths=paths,
            role=ModelRole(**defaults["models"]["weekly"]),
            prompt_name=f"{kind}.md",
            evidence=evidence,
            run_id=run_id,
            max_packet_chars=defaults["limits"]["max_packet_chars"],
            schema_name="text-output.schema.json",
        )
        store.finish_run(run_id, "completed", evidence_count=len(evidence), receipt_path=str(receipt))
        return result
    except Exception as error:
        store.finish_run(run_id, "failed", evidence_count=len(evidence), error=str(error))
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "setup":
            paths = setup_runtime()
            executable = install_standalone_codex(paths)
            _json({"runtime": str(paths.root), "codex": str(executable), "next": "sb auth login"})
        elif args.command == "auth":
            paths = setup_runtime()
            return login_dedicated_account(paths)
        elif args.command == "bootstrap":
            if args.approve and args.refresh_evidence:
                raise RuntimeError("Choose either --approve or --refresh-evidence, not both.")
            if args.approve:
                _json(approve_bootstrap())
            elif args.refresh_evidence:
                _json(refresh_bootstrap_evidence())
            else:
                _json(bootstrap(linkedin_export=args.linkedin_export))
        elif args.command in {"daily", "weekly"}:
            _json(incremental(args.command))
        elif args.command == "scheduled":
            _json(scheduled())
        elif args.command == "refresh-project":
            _json(refresh_project(args.project))
        elif args.command == "review":
            paths, _config, _defaults, store = _common()
            pending = store.observations("pending")
            if args.action in {None, "list"}:
                _json(pending if getattr(args, "raw", False) else review_summary(pending))
            elif args.action == "digest":
                if store.bootstrap_state()["state"] == "awaiting_review":
                    _json(write_bootstrap_review_artifacts(vault_root(), store))
                else:
                    _json(write_review_artifacts(vault_root(), store))
            elif args.action == "show":
                if args.id.startswith("obs-"):
                    item = store.observation(args.id)
                    if item is None:
                        raise KeyError(args.id)
                    _json(item)
                else:
                    _json(find_review_group(pending, args.id))
            elif args.action == "approve":
                _json(decide_review(args.id, "approved"))
            elif args.action == "resolve":
                _json(decide_review(args.id, "resolved", reason=args.answer))
            elif args.action == "answer":
                group = find_review_group(pending, args.group)
                if group["mode"] != "answer":
                    raise RuntimeError("Only clarification groups accept answers.")
                if args.item < 1 or args.item > group["count"]:
                    raise IndexError(f"Question number must be between 1 and {group['count']}")
                observation_id = group["items"][args.item - 1]["id"]
                _json(decide_review(observation_id, "resolved", reason=args.answer))
            elif args.action == "approve-group":
                _json(decide_review_group(args.id, "approved"))
            elif args.action == "reject-group":
                _json(decide_review_group(args.id, "rejected", reason=args.reason))
            else:
                _json(decide_review(args.id, "rejected", reason=args.reason))
        elif args.command == "interview":
            paths, _config, _defaults, store = _common()
            create_interview(vault_root(), paths.root)
            if args.action in {None, "status"}:
                _json(interview_status(paths.root))
            elif args.action == "next":
                status_path = paths.root / "interview.json"
                data = json.loads(status_path.read_text(encoding="utf-8"))
                unanswered = [(key, question) for key, question in QUESTIONS if key not in data.get("answers", {})]
                _json({"next": unanswered[0] if unanswered else None})
            else:
                complete = answer_interview(store, paths.root, args.question_id, args.answer)
                create_interview(vault_root(), paths.root)
                _json({"question": args.question_id, "saved": True, "complete": complete})
        elif args.command == "search":
            paths, *_ = _common()
            _json(search(paths, vault_root(), args.query, limit=args.limit))
        elif args.command in {"write-as-me", "career"}:
            _json(_draft(args.command, args.request))
        elif args.command == "reindex":
            paths, *_ = _common()
            print(reindex(paths, vault_root()))
        elif args.command == "health":
            paths, *_ = _common()
            path = write_report(paths)
            _json({"report": str(path), "health": health_report(paths)})
        elif args.command == "models":
            paths, _config, defaults, _store = _common()
            _json(canary(paths, defaults["models"]))
        elif args.command == "protocol":
            paths, config, _defaults, store = _common()
            if store.bootstrap_state()["state"] != "completed":
                raise RuntimeError("Protocol publishing is gated until bootstrap approval.")
            _json(
                sync_protocol_draft(
                    vault_root(),
                    paths,
                    config["public_repository"],
                    store,
                    if_changed=args.if_changed,
                )
            )
        elif args.command == "dashboard":
            paths = dashboard_runtime()
            if args.action == "build":
                path = build_dashboard(paths, vault_root())
                _json({"status": "built", "dashboard": str(path)})
            elif args.action == "install":
                shortcut = install_dashboard_shortcut(paths, vault_root())
                _json({"status": "installed", "shortcut": str(shortcut)})
            elif args.action == "serve":
                serve_dashboard(paths, vault_root(), open_browser=not args.no_browser)
            else:
                url = open_dashboard(paths, vault_root())
                _json({"status": "opened", "dashboard": url})
        return 0
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
