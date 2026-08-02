from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from .basic_memory_integration import reindex
from .config import (
    RuntimePaths,
    dashboard_runtime,
    load_defaults,
    load_runtime_config,
    set_model_runtime,
    setup_runtime,
    vault_root,
)
from .curate import (
    CURATE_KINDS,
    KNOWLEDGE_LAYER_ORDER,
    add_curate_candidate,
    correct_project_knowledge_attribution,
    require_main_vault_context,
)
from .dashboard import build_dashboard, install_dashboard_shortcut, open_dashboard
from .dashboard_server import serve_dashboard
from .evaluation_corpus import load_evaluation_corpus
from .evaluation_harness import EvaluationHarness
from .extraction_harness import ExtractionHarness
from .extraction_model import (
    EXTRACTION_ITEM_ENVELOPE_CHARS,
    BudgetedExtractionModel,
    ProtocolExtractionModel,
)
from .gitops import sync_protocol_draft
from .health import report as health_report, write_report
from .installer import install_standalone_codex, login_dedicated_account
from .model_runner import ModelRole, canary, run_model, usage_from_receipt
from .orchestrator import (
    analyze_project_sessions,
    approve_bootstrap,
    bootstrap,
    decide_review,
    decide_review_group,
    incremental,
    reconcile_project_sessions,
    refresh_bootstrap_evidence,
    refresh_project,
    rebuild_projects,
    scheduled,
    sync_project_index,
)
from .recall_benchmark import RecallBenchmark
from .recall_corpus import load_recall_corpus
from .profile import QUESTIONS, answer_interview, create_interview, interview_status
from .project_forgetting import forget_projects
from .publisher import (
    refresh_learning_tracker,
    write_bootstrap_review_artifacts,
    write_review_artifacts,
)
from .question_actions import (
    answer_question,
    attribute_question,
    dismiss_question,
    undo_last_question_dismissal,
)
from .review import find_review_group, review_summary
from .service import (
    configure_session_project_link,
    latest_sessions,
    remember_explicit_skill,
    search,
    session_index_summary,
)
from .state import StateStore


def _json(value: object) -> None:
    encoding = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
    ensure_ascii = encoding not in {"utf8", "utf_8"}
    print(json.dumps(value, indent=2, ensure_ascii=ensure_ascii, default=str))


def _common() -> tuple[RuntimePaths, dict, dict, StateStore]:
    paths = setup_runtime()
    config = load_runtime_config(paths)
    return paths, config, load_defaults(config), StateStore(paths.state)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sb", description="Evidence-backed Personal Second Brain Protocol"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("setup")
    auth = sub.add_parser("auth")
    auth.add_subparsers(dest="action", required=True).add_parser("login")
    boot = sub.add_parser("bootstrap")
    boot.add_argument(
        "--linkedin-export", "--linkedin-pdf", dest="linkedin_export", type=Path
    )
    boot.add_argument("--approve", action="store_true")
    boot.add_argument("--refresh-evidence", action="store_true")
    daily = sub.add_parser("daily")
    daily.add_argument(
        "--owner-requested",
        action="store_true",
        help="Authorize this one manual Daily because the vault owner explicitly requested it.",
    )
    daily.add_argument(
        "--test",
        action="store_true",
        help="Publish the Daily locally without snapshotting, committing, or pushing Git.",
    )
    sub.add_parser("weekly")
    sub.add_parser("scheduled")
    cutover = sub.add_parser("cutover")
    cutover_daily_weekly = cutover.add_subparsers(
        dest="target", required=True
    ).add_parser("daily-weekly")
    cutover_daily_weekly.add_argument(
        "--owner-authorized",
        action="store_true",
        help="Confirm the vault owner explicitly authorized the governed cutover.",
    )
    refresh = sub.add_parser("refresh-project")
    refresh.add_argument("project")
    sub.add_parser("sync-project-index")
    rebuild = sub.add_parser("rebuild-projects")
    rebuild.add_argument("--collection", action="append", default=[])
    rebuild.add_argument("--confirm", action="store_true")
    forget = sub.add_parser("forget-project")
    forget.add_argument("project")
    forget.add_argument("--include", action="append", default=[])
    forget.add_argument("--protect", action="append", default=[])
    forget.add_argument("--confirm", action="store_true")
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
    reclassify = review_sub.add_parser("reclassify")
    reclassify.add_argument("id")
    reclassify.add_argument("--kind", required=True)
    reclassify.add_argument("--reason", required=True)
    reattribute = review_sub.add_parser("correct-project-attribution")
    reattribute.add_argument("id", nargs="+")
    reattribute.add_argument("--project", required=True)
    reattribute.add_argument("--replace-project-name")
    reattribute.add_argument("--reason", required=True)
    dismiss = review_sub.add_parser("dismiss")
    dismiss.add_argument("id")
    review_sub.add_parser("undo-dismiss")
    resolve = review_sub.add_parser("resolve")
    resolve.add_argument("id")
    resolve.add_argument("--answer", required=True)
    review_answer = review_sub.add_parser("answer")
    review_answer.add_argument("group")
    review_answer.add_argument("item", type=int)
    review_answer.add_argument("--answer", required=True)
    attribute = review_sub.add_parser("attribute-question")
    attribute.add_argument("id")
    attribute.add_argument("project_id")
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
    sessions = sub.add_parser("sessions")
    sessions_sub = sessions.add_subparsers(dest="action")
    sessions_latest = sessions_sub.add_parser("latest")
    sessions_latest.add_argument("--surface", choices=("codex", "antigravity"))
    sessions_latest.add_argument("--project")
    sessions_latest.add_argument("--limit", type=int, default=5)
    sessions_sub.add_parser("index")
    sessions_analyze = sessions_sub.add_parser("analyze")
    sessions_analyze.add_argument("--force", action="store_true")
    sessions_sub.add_parser("reconcile")
    sessions_link = sessions_sub.add_parser("link")
    sessions_link.add_argument("surface", choices=("codex", "antigravity"))
    sessions_link.add_argument("session_id")
    sessions_link.add_argument("project")
    sessions_link.add_argument("--confirm", action="store_true")
    sessions.set_defaults(action="latest")
    remember_skill = sub.add_parser("remember-skill")
    remember_skill.add_argument("skill_id")
    remember_skill.add_argument("name")
    remember_skill.add_argument("claim")
    remember_skill.add_argument("--evidence", action="append", default=[])
    remember_skill.add_argument("--successful-implementation", action="store_true")
    learning = sub.add_parser("learning")
    learning_sub = learning.add_subparsers(dest="action", required=True)
    learning_suppress = learning_sub.add_parser("suppress")
    learning_suppress.add_argument("topic")
    curate = sub.add_parser("curate")
    curate_sub = curate.add_subparsers(dest="action", required=True)
    curate_add = curate_sub.add_parser("add")
    curate_add.add_argument("--layer", required=True, choices=KNOWLEDGE_LAYER_ORDER)
    curate_add.add_argument("--kind", required=True, choices=CURATE_KINDS)
    curate_add.add_argument("--subject", required=True)
    curate_add.add_argument("--claim", required=True)
    curate_add.add_argument("--project")
    curate_add.add_argument("--cross-project", action="store_true")
    curate_add.add_argument("--confidence", type=float, default=0.85)
    curate_add.add_argument("--explicit", action="store_true")
    curate_add.add_argument("--confirmed", action="store_true")
    writer = sub.add_parser("write-as-me")
    writer.add_argument("request")
    career = sub.add_parser("career")
    career.add_argument("request")
    sub.add_parser("reindex")
    sub.add_parser("health")
    models = sub.add_parser("models")
    model_actions = models.add_subparsers(dest="action", required=True)
    model_actions.add_parser("check")
    model_runtime = model_actions.add_parser("set-runtime")
    model_runtime.add_argument(
        "--provider", required=True, choices=("openai", "openrouter")
    )
    model_runtime.add_argument(
        "--policy",
        required=True,
        choices=("chatgpt-direct-v1", "openrouter-hybrid-v1"),
    )
    protocol = sub.add_parser("protocol")
    protocol_publish = protocol.add_subparsers(dest="action", required=True).add_parser(
        "publish"
    )
    protocol_publish.add_argument("--if-changed", action="store_true")
    dashboard = sub.add_parser("dashboard")
    dashboard_sub = dashboard.add_subparsers(dest="action")
    dashboard_sub.add_parser("build")
    dashboard_sub.add_parser("open")
    dashboard_sub.add_parser("install")
    dashboard_serve = dashboard_sub.add_parser("serve")
    dashboard_serve.add_argument("--no-browser", action="store_true")
    dashboard.set_defaults(action="open")
    harness = sub.add_parser("harness")
    harness_sub = harness.add_subparsers(dest="action", required=True)
    harness_evaluate = harness_sub.add_parser("evaluate")
    harness_evaluate.add_argument(
        "--corpus",
        choices=("policy", "quality"),
        default="policy",
    )
    harness_evaluate.add_argument("--live", action="store_true")
    harness_evaluate.add_argument("--max-model-calls", type=int)
    harness_evaluate.add_argument("--confirm-cost", action="store_true")
    harness_sub.add_parser("recall")
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
        return {
            "content": "No verified canonical context was found for this request.",
            "evidence_refs": [],
        }
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
        store.finish_run(
            run_id,
            "completed",
            evidence_count=len(evidence),
            receipt_path=str(receipt),
            usage=usage_from_receipt(receipt),
        )
        return result
    except Exception as error:
        store.finish_run(
            run_id, "failed", evidence_count=len(evidence), error=str(error)
        )
        raise


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "setup":
            paths = setup_runtime()
            executable = install_standalone_codex(paths)
            _json(
                {
                    "runtime": str(paths.root),
                    "codex": str(executable),
                    "next": "sb auth login",
                }
            )
        elif args.command == "auth":
            paths = setup_runtime()
            return login_dedicated_account(paths)
        elif args.command == "bootstrap":
            if args.approve and args.refresh_evidence:
                raise RuntimeError(
                    "Choose either --approve or --refresh-evidence, not both."
                )
            if args.approve:
                _json(approve_bootstrap())
            elif args.refresh_evidence:
                _json(refresh_bootstrap_evidence())
            else:
                _json(bootstrap(linkedin_export=args.linkedin_export))
        elif args.command == "daily":
            _json(
                incremental(
                    "daily",
                    manual_authorized=args.owner_requested,
                    publish_git=not args.test,
                )
            )
        elif args.command == "weekly":
            _json(incremental("weekly"))
        elif args.command == "scheduled":
            _json(scheduled())
        elif args.command == "cutover":
            if not args.owner_authorized:
                raise RuntimeError(
                    "Daily/Weekly cutover requires explicit owner authorization."
                )
            paths, _config, _defaults, store = _common()
            if store.bootstrap_state()["state"] != "completed":
                raise RuntimeError("Complete bootstrap before Daily/Weekly cutover.")
            backup = (
                paths.runs
                / "backups"
                / (
                    "state-pre-governed-cutover-"
                    + datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
                    + ".sqlite"
                )
            )
            store.backup(backup)
            result = store.establish_governed_cutover_baseline(
                reason=(
                    "Owner-authorized governed Daily/Weekly cutover: archive the "
                    "pre-cutover active queue and resurface only on material new evidence."
                )
            )
            review = write_review_artifacts(vault_root(), store)
            dashboard_path = build_dashboard(paths, vault_root())
            reindex(paths, vault_root())
            _json(
                {
                    **result,
                    "engine": "governed-extraction-v3",
                    "state_backup": str(backup),
                    "review_path": review.get("review_path"),
                    "dashboard_path": str(dashboard_path),
                    "canonical_knowledge_deleted": False,
                }
            )
        elif args.command == "refresh-project":
            _json(refresh_project(args.project))
        elif args.command == "sync-project-index":
            _json(sync_project_index())
        elif args.command == "rebuild-projects":
            _json(
                rebuild_projects(
                    collection_names=args.collection or None,
                    confirm=args.confirm,
                )
            )
        elif args.command == "forget-project":
            paths, _config, _defaults, store = _common()
            _json(
                forget_projects(
                    paths,
                    vault_root(),
                    store,
                    identifiers=[args.project, *args.include],
                    protected_identifiers=args.protect,
                    confirm=args.confirm,
                )
            )
        elif args.command == "sessions" and args.action == "link":
            paths, _config, _defaults, store = _common()
            result = configure_session_project_link(
                paths,
                store,
                surface=args.surface,
                session_id=args.session_id,
                project=args.project,
                confirm=args.confirm,
            )
            if args.confirm:
                result["reconciliation"] = reconcile_project_sessions()
            _json(result)
        elif args.command == "remember-skill":
            paths, _config, _defaults, store = _common()
            result = remember_explicit_skill(
                vault_root(),
                store,
                skill_id=args.skill_id,
                name=args.name,
                claim=args.claim,
                supporting_evidence=args.evidence,
                successful_implementation=args.successful_implementation,
            )
            reindex(paths, vault_root())
            build_dashboard(paths, vault_root())
            _json(result)
        elif args.command == "learning":
            paths, _config, _defaults, store = _common()
            result = store.suppress_learning_topic_until_new(args.topic)
            result["path"] = str(refresh_learning_tracker(vault_root(), store))
            build_dashboard(paths, vault_root())
            try:
                reindex(paths, vault_root())
                result["search_refresh"] = "completed"
            except Exception as error:
                result["search_refresh"] = f"deferred: {type(error).__name__}"
            _json(result)
        elif args.command == "curate":
            paths, _config, _defaults, store = _common()
            require_main_vault_context(vault_root())
            result = add_curate_candidate(
                vault_root(),
                store,
                layer=args.layer,
                kind=args.kind,
                subject=args.subject,
                claim=args.claim,
                project=args.project,
                confidence=args.confidence,
                explicit=args.explicit,
                confirmed=args.confirmed,
                allow_cross_project=args.cross_project,
            )
            build_dashboard(paths, vault_root())
            try:
                reindex(paths, vault_root())
                result["search_refresh"] = "completed"
            except Exception as error:
                result["search_refresh"] = f"deferred: {type(error).__name__}"
            _json(result)
        elif args.command == "review":
            paths, _config, _defaults, store = _common()
            pending = store.observations("pending")
            if args.action in {None, "list"}:
                _json(
                    pending if getattr(args, "raw", False) else review_summary(pending)
                )
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
            elif args.action == "reclassify":
                replacement_id = store.reclassify_observation(
                    args.id,
                    kind=args.kind,
                    reason=args.reason,
                )
                if store.bootstrap_state()["state"] == "awaiting_review":
                    write_bootstrap_review_artifacts(vault_root(), store)
                else:
                    write_review_artifacts(vault_root(), store)
                build_dashboard(paths, vault_root())
                _json(
                    {
                        "id": args.id,
                        "status": "reclassified",
                        "replacement_id": replacement_id,
                        "kind": args.kind,
                    }
                )
            elif args.action == "correct-project-attribution":
                results = []
                for observation_id in args.id:
                    observation = store.observation(observation_id)
                    replacement = (
                        args.replace_project_name
                        if observation is not None
                        and args.replace_project_name
                        and args.replace_project_name in str(observation["claim"])
                        else None
                    )
                    results.append(
                        correct_project_knowledge_attribution(
                            vault_root(),
                            store,
                            observation_id=observation_id,
                            project=args.project,
                            replace_project_name=replacement,
                            reason=args.reason,
                        )
                    )
                if store.bootstrap_state()["state"] == "awaiting_review":
                    write_bootstrap_review_artifacts(vault_root(), store)
                else:
                    write_review_artifacts(vault_root(), store)
                build_dashboard(paths, vault_root())
                try:
                    reindex(paths, vault_root())
                    search_refresh = "completed"
                except Exception as error:
                    search_refresh = f"deferred: {type(error).__name__}"
                _json({"status": "corrected", "items": results, "search_refresh": search_refresh})
            elif args.action == "resolve":
                _json(
                    answer_question(
                        vault_root(),
                        store,
                        args.id,
                        args.answer,
                        paths=paths,
                    )
                )
            elif args.action == "answer":
                group = find_review_group(pending, args.group)
                if group["mode"] != "answer":
                    raise RuntimeError("Only clarification groups accept answers.")
                if args.item < 1 or args.item > group["count"]:
                    raise IndexError(
                        f"Question number must be between 1 and {group['count']}"
                    )
                observation_id = group["items"][args.item - 1]["id"]
                _json(
                    answer_question(
                        vault_root(),
                        store,
                        observation_id,
                        args.answer,
                        paths=paths,
                    )
                )
            elif args.action == "attribute-question":
                _json(attribute_question(vault_root(), store, args.id, args.project_id))
            elif args.action == "dismiss":
                _json(dismiss_question(vault_root(), store, args.id))
            elif args.action == "undo-dismiss":
                _json(undo_last_question_dismissal(vault_root(), store))
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
                unanswered = [
                    (key, question)
                    for key, question in QUESTIONS
                    if key not in data.get("answers", {})
                ]
                _json({"next": unanswered[0] if unanswered else None})
            else:
                complete = answer_interview(
                    store, paths.root, args.question_id, args.answer
                )
                create_interview(vault_root(), paths.root)
                _json(
                    {"question": args.question_id, "saved": True, "complete": complete}
                )
        elif args.command == "search":
            paths, *_ = _common()
            _json(search(paths, vault_root(), args.query, limit=args.limit))
        elif args.command == "sessions":
            _paths, _config, _defaults, store = _common()
            if args.action == "reconcile":
                _json(reconcile_project_sessions())
            elif args.action == "analyze":
                _json(analyze_project_sessions(force=args.force))
            elif args.action == "index":
                _json(session_index_summary(store))
            else:
                _json(
                    latest_sessions(
                        store,
                        surface=args.surface,
                        project=args.project,
                        limit=args.limit,
                    )
                )
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
            if args.action == "set-runtime":
                _json(set_model_runtime(args.provider, args.policy))
            else:
                paths, _config, defaults, _store = _common()
                _json(canary(paths, defaults["models"]))
        elif args.command == "harness":
            if args.action == "recall":
                corpus = load_recall_corpus(
                    Path(__file__).resolve().parents[2]
                    / "evaluation"
                    / "corpora"
                    / "recall-ablation.json"
                )
                _json(
                    asdict(
                        RecallBenchmark(retriever=corpus.harness()).evaluate(
                            corpus.suite
                        )
                    )
                )
                return 0
            corpus_names = {
                "policy": "extraction-policy-adversarial.json",
                "quality": "extraction-quality.json",
            }
            corpus = load_evaluation_corpus(
                Path(__file__).resolve().parents[2]
                / "evaluation"
                / "corpora"
                / corpus_names[args.corpus]
            )
            if not args.live:
                report = EvaluationHarness(
                    extractor=ExtractionHarness(model=corpus.replay_model())
                ).evaluate(corpus.suite)
                _json(asdict(report))
                if not report.passed:
                    return 1
            else:
                if not args.confirm_cost or not args.max_model_calls:
                    raise RuntimeError(
                        "Live evaluation requires --confirm-cost and a positive "
                        "--max-model-calls ceiling."
                    )
                if args.max_model_calls <= 0:
                    raise RuntimeError("--max-model-calls must be positive.")
                if not corpus.live_case_count:
                    raise RuntimeError("This corpus has no live-enabled cases.")
                paths, _config, defaults, store = _common()
                role = ModelRole(**defaults["models"]["daily"])
                run_id = store.start_run(
                    "extraction-evaluation",
                    role.name,
                    role.reasoning,
                )
                try:
                    model = BudgetedExtractionModel(
                        delegate=ProtocolExtractionModel(
                            paths=paths,
                            role=role,
                            run_id=run_id,
                            max_packet_chars=defaults["limits"]["max_packet_chars"],
                            max_evidence_chars=defaults["limits"][
                                "max_evidence_text_chars"
                            ]
                            + EXTRACTION_ITEM_ENVELOPE_CHARS,
                            use_cache=False,
                        ),
                        max_model_calls=args.max_model_calls,
                    )
                    report = EvaluationHarness(
                        extractor=ExtractionHarness(model=model)
                    ).evaluate(corpus.live_suite)
                    store.finish_run(
                        run_id,
                        "completed" if report.passed else "failed",
                        evidence_count=report.source_evidence_count,
                        error=None if report.passed else "evaluation-mismatch",
                        usage={
                            "input_tokens": report.input_tokens,
                            "output_tokens": report.output_tokens,
                            "total_tokens": report.total_tokens,
                            "model_calls": report.model_calls,
                        },
                    )
                    _json(asdict(report))
                    if not report.passed:
                        return 1
                except Exception as error:
                    store.finish_run(run_id, "failed", error=type(error).__name__)
                    raise
        elif args.command == "protocol":
            paths, config, _defaults, store = _common()
            if store.bootstrap_state()["state"] != "completed":
                raise RuntimeError(
                    "Protocol publishing is gated until bootstrap approval."
                )
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
