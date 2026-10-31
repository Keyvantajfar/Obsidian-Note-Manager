"""Headless command line on top of the same core the TUI uses (scripting / cron / quick checks).

    vaultmgr-cli VAULT status
    vaultmgr-cli VAULT tag   [--route] [--workers N] [--mock]
    vaultmgr-cli VAULT stage
    vaultmgr-cli VAULT accept [--min-confidence 0.85]
    vaultmgr-cli VAULT apply  [--dry-run]
    vaultmgr-cli VAULT undo
    vaultmgr-cli VAULT auto   [--route]      # tag (+route), accept confident ones, apply
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from .mock import MockLLM
from .session import Session


def _print_plan(plan) -> None:
    c = plan.counts()
    print(f"plan: {c['headers']} header write(s), {c['moves']} move(s), {c['new_folders']} new folder(s), "
          f"{c['blocked']} blocked, {c['warnings']} with warnings, {c['noop']} no-op")
    for it in plan.items:
        tag = "BLOCK" if it.blocked else ("warn " if it.warned else "ok   ")
        dst = f" → {it.dst}" if it.dst else ""
        print(f"  [{tag}] {it.kind:6} {it.note}{dst}")
        for i in it.issues:
            if i.level != "info":
                print(f"           {i.level}: {i.text}")


async def _run(args) -> int:
    if args.import_config:
        from .settings import Profiles
        prof = Profiles.load().import_legacy(args.import_config)
        print(f"imported LLM profile '{prof.name}' (model {prof.model or '-'})")
    s = Session(args.vault, llm=MockLLM() if args.mock else None)
    if args.workers:
        s.settings.workers = args.workers
    s.scan()
    cmd = args.cmd
    try:
        if cmd == "status":
            st = s.status()
            print(f"{st['notes']} notes · {st['incomplete']} need headers ({st['no_header']} without any) · "
                  f"{st['folders']} folders · stage {st['stage']}")
        elif cmd in ("tag", "auto"):
            job = s.run_ai(route=args.route or len(s.taxonomy) > 0, only_unstaged=True)
            if job is None:
                print("nothing to do")
            else:
                last = -1
                while job.active:
                    await asyncio.sleep(0.3)
                    if job.finished_count != last:
                        last = job.finished_count
                        print(f"\r{job.finished_count}/{job.total}  {job.rate:.1f}/s  errors {len(job.errors)}", end="", flush=True)
                print(f"\r{job.finished_count}/{job.total} done · errors {len(job.errors)} · tokens {job.tokens}")
                for e in (job.errors if args.debug_llm else job.errors[:10]):
                    print(f"  ✗ {e.key}: {e.message}")
            if cmd == "auto":
                n = s.accept_all(min_confidence=s.settings.auto_accept_threshold)
                print(f"accepted {n} confident change(s); {len(s.stage.all(status='proposed'))} left for review")
                plan = s.plan_apply()
                _print_plan(plan)
                if not args.dry_run:
                    res = s.apply(plan, label="cli auto")
                    print(f"applied {len(res.applied)} · failed {len(res.failed)} · batch {res.batch_id}")
        elif cmd == "stage":
            for c in s.stage.all():
                conf = f"{c.confidence:.2f}" if c.confidence is not None else " -- "
                print(f"{c.status:9} {c.source:6} {conf} {c.kind:6} {c.note}  {c.summary()}")
        elif cmd == "accept":
            n = s.accept_all(min_confidence=args.min_confidence)
            print(f"accepted {n}")
        elif cmd == "apply":
            plan = s.plan_apply()
            _print_plan(plan)
            if not args.dry_run:
                res = s.apply(plan)
                print(f"applied {len(res.applied)} · failed {len(res.failed)} · batch {res.batch_id}")
                for cid, msg in res.failed:
                    print(f"  ✗ {cid}: {msg}")
        elif cmd == "undo":
            r = s.undo()
            print(f"undid batch {r.batch_id or '(none)'}: {r.restored} header(s) restored, {r.moved_back} move(s) reverted")
            for c in r.conflicts:
                print(f"  ! {c}")
    finally:
        await s.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="vaultmgr-cli", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("vault")
    p.add_argument("cmd", choices=["status", "tag", "stage", "accept", "apply", "undo", "auto"])
    p.add_argument("--route", action="store_true", help="also route notes into the planned folders")
    p.add_argument("--workers", type=int, default=0)
    p.add_argument("--min-confidence", type=float, default=None)
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--import-config", metavar="FILE", help="import LLM settings from an old config.json first")
    p.add_argument("--debug-llm", action="store_true", help="print every failed note's error")
    p.add_argument("--mock", action="store_true", help="use the built-in fake model (no server)")
    args = p.parse_args(argv)
    return asyncio.run(_run(args))


if __name__ == "__main__":
    sys.exit(main())
