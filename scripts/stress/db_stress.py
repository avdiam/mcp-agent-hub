"""
db-level stress / correctness harness for the MCP Agent Hub queue.

Drives mcp_hub.db directly (no HTTP) against a throwaway temp DB, so it is safe
to run while the live hub is up. Two scenarios:

  1. atomic-claim correctness (D4): N concurrent claimers drain M messages for one
     recipient; asserts no message is claimed twice (at-least-once must never become
     at-least-twice-simultaneously) and every message is accounted for.
  2. concurrent-writer contention (D9/WAL): K workers each loop enqueue -> claim ->
     complete simultaneously; counts "database is locked" errors and measures throughput.

Usage (from repo root):
  PYTHONPATH=. ./venv/Scripts/python.exe scripts/stress/db_stress.py --claimers 32 --messages 2000 --writers 24 --writer-ops 200
"""
import argparse
import asyncio
import collections
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
from mcp_hub import db  # noqa: E402


def _is_locked(exc: Exception) -> bool:
    return "database is locked" in str(exc).lower() or "database is busy" in str(exc).lower()


async def scenario_atomic_claim(db_path, n_claimers, n_messages):
    await db.upsert_agent(db_path, "sender", "[]")
    await db.upsert_agent(db_path, "recipient", "[]")
    for i in range(n_messages):
        await db.enqueue_message(db_path, "sender", "recipient", f"msg-{i}")

    claimed_by = collections.defaultdict(list)   # claimer_id -> [message_id, ...]
    lock_errors = 0
    total_claimed = 0
    stop = asyncio.Event()

    async def claimer(cid):
        nonlocal lock_errors, total_claimed
        idle_rounds = 0
        while not stop.is_set():
            try:
                rows = await db.claim_pending(db_path, "recipient")
            except Exception as e:
                if _is_locked(e):
                    lock_errors += 1
                    await asyncio.sleep(0.003)
                    continue
                raise
            if rows:
                idle_rounds = 0
                for r in rows:
                    claimed_by[cid].append(r["id"])
                total_claimed += len(rows)
                if total_claimed >= n_messages:
                    stop.set()
            else:
                idle_rounds += 1
                if idle_rounds >= 3:
                    break
                await asyncio.sleep(0.002)

    t0 = time.perf_counter()
    await asyncio.gather(*(claimer(i) for i in range(n_claimers)))
    elapsed = time.perf_counter() - t0

    all_ids = [mid for ids in claimed_by.values() for mid in ids]
    unique_ids = set(all_ids)
    dupes = len(all_ids) - len(unique_ids)

    print("--- Scenario 1: atomic-claim correctness ---")
    print(f"  claimers={n_claimers}  messages={n_messages}")
    print(f"  claimed total      : {len(all_ids)}")
    print(f"  unique messages    : {len(unique_ids)}")
    print(f"  DOUBLE-CLAIMS      : {dupes}   <- MUST be 0")
    print(f"  unclaimed (lost)   : {n_messages - len(unique_ids)}   <- MUST be 0")
    print(f"  lock errors        : {lock_errors}")
    print(f"  elapsed            : {elapsed*1000:.0f} ms  ({len(unique_ids)/elapsed:.0f} claims/s)")
    ok = dupes == 0 and len(unique_ids) == n_messages
    print(f"  RESULT             : {'PASS' if ok else 'FAIL'}")
    return ok


async def scenario_writer_contention(db_path, n_writers, ops_per_writer):
    for i in range(n_writers):
        await db.upsert_agent(db_path, f"w{i}", "[]")
    await db.upsert_agent(db_path, "sink", "[]")

    lock_errors = 0
    other_errors = []
    ops_done = 0

    async def worker(wid):
        nonlocal lock_errors, ops_done
        for _ in range(ops_per_writer):
            try:
                res = await db.enqueue_message(db_path, f"w{wid}", "sink", "x")
                claimed = await db.claim_pending(db_path, "sink")
                for r in claimed:
                    if r["kind"] == "task":
                        await db.complete_message(db_path, r["id"], "ok")
                ops_done += 1
            except Exception as e:
                if _is_locked(e):
                    lock_errors += 1
                else:
                    other_errors.append(str(e))

    t0 = time.perf_counter()
    await asyncio.gather(*(worker(i) for i in range(n_writers)))
    elapsed = time.perf_counter() - t0

    attempted = n_writers * ops_per_writer
    print("--- Scenario 2: concurrent-writer contention ---")
    print(f"  writers={n_writers}  ops/writer={ops_per_writer}  attempted={attempted}")
    print(f"  ops completed      : {ops_done}")
    print(f"  LOCK ERRORS        : {lock_errors}   <- 'database is locked' (busy_timeout/retry gap)")
    print(f"  other errors       : {len(other_errors)}" + (f" e.g. {other_errors[0]}" if other_errors else ""))
    print(f"  elapsed            : {elapsed*1000:.0f} ms  ({ops_done/elapsed:.0f} ops/s)")
    return lock_errors


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claimers", type=int, default=32)
    ap.add_argument("--messages", type=int, default=2000)
    ap.add_argument("--writers", type=int, default=24)
    ap.add_argument("--writer-ops", type=int, default=200)
    args = ap.parse_args()

    tmpdir = tempfile.mkdtemp(prefix="hubstress_")
    db_path = os.path.join(tmpdir, "stress.db")
    await db.init_db(db_path)
    print(f"temp db: {db_path}\n")

    ok = await scenario_atomic_claim(db_path, args.claimers, args.messages)
    print()
    locks = await scenario_writer_contention(db_path, args.writers, args.writer_ops)

    print("\n=== SUMMARY ===")
    print(f"  atomic-claim correctness : {'PASS' if ok else 'FAIL'}")
    print(f"  writer lock errors       : {locks}")


if __name__ == "__main__":
    asyncio.run(main())
