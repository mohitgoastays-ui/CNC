"""
Worker Runner — consumes the Redis job queue, dispatches to the right worker.
In production, run multiple instances for throughput.
CPU workers handle heavy_toolpath; GPU workers handle ai_depth.

Reliability model
-----------------
Jobs are moved with BRPOPLPUSH onto a per-worker in-flight list, so a worker
that dies mid-job leaves the message recoverable instead of losing it. A job is
only removed from the in-flight list once it reaches a terminal state; anything
that fails MAX_RETRIES times goes to the dead-letter list.
"""
import os, json, time, socket, traceback
import redis
from datetime import datetime

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
WORKER_TYPE = os.getenv("WORKER_TYPE", "cpu")           # "cpu" or "gpu"
MAX_RETRIES = int(os.getenv("WORKER_MAX_RETRIES", "3"))
QUEUE = "relief:jobs"
DEAD = "relief:jobs:dead"
INFLIGHT = f"relief:jobs:inflight:{WORKER_TYPE}:{socket.gethostname()}:{os.getpid()}"

# W1 FIX: routing a job this worker cannot run used to rpush it straight back
# and `continue`. With one CPU worker and a single GPU job queued, blpop
# returned instantly every time, so the process spun at 100% CPU forever. Park
# mismatched jobs on a type-specific queue instead of hot-looping on them.
OTHER = {"cpu": "relief:jobs:gpu", "gpu": "relief:jobs:cpu"}[WORKER_TYPE]
MINE = f"relief:jobs:{WORKER_TYPE}"


def _terminal(r, raw):
    """Remove a finished message from this worker's in-flight list."""
    try:
        r.lrem(INFLIGHT, 1, raw)
    except Exception:
        pass


def _requeue_stale(r):
    """Recover anything this worker left in flight from a previous crash."""
    moved = 0
    while True:
        raw = r.rpoplpush(INFLIGHT, QUEUE)
        if not raw:
            break
        moved += 1
    if moved:
        print(f"[worker:{WORKER_TYPE}] recovered {moved} in-flight job(s) after restart", flush=True)


def handle(raw, r, SessionLocal, Job, JobStatus, JobType):
    payload = json.loads(raw)
    job_id = payload["job_id"]
    attempt = int(payload.get("attempt", 0))
    print(f"[worker:{WORKER_TYPE}] picked up {job_id} (attempt {attempt + 1})", flush=True)

    db = SessionLocal()
    job = None
    try:
        job = db.query(Job).filter(Job.id == job_id).first()
        if not job:
            # W2 FIX: an unknown job id was dropped on the floor with no record.
            print(f"[worker] job {job_id} not in DB -> dead letter", flush=True)
            r.rpush(DEAD, raw)
            return

        if job.job_type == JobType.HEAVY_TOOLPATH and WORKER_TYPE == "cpu":
            from workers.heavy_toolpath import run_heavy_toolpath
            runner = run_heavy_toolpath
        elif job.job_type == JobType.AI_DEPTH and WORKER_TYPE == "gpu":
            from workers.ai_depth import run_ai_depth
            runner = run_ai_depth
        else:
            r.rpush(OTHER, raw)          # park, do not spin
            return

        job.status = JobStatus.PROCESSING
        db.commit()
        runner(job, db)
        job.status = JobStatus.DONE
        job.progress = 100
        job.completed_at = datetime.utcnow()
        db.commit()
        print(f"[worker:{WORKER_TYPE}] job {job_id} completed", flush=True)

    except Exception as e:
        traceback.print_exc()
        # W3 FIX: the old handler referenced `job` unconditionally. If the DB
        # query itself threw, `job` was unbound and the handler raised
        # NameError, which escaped the outer try (it caught only ConnectionError
        # and KeyboardInterrupt) and killed the worker process outright.
        if attempt + 1 < MAX_RETRIES:
            payload["attempt"] = attempt + 1
            r.rpush(QUEUE, json.dumps(payload))
            print(f"[worker:{WORKER_TYPE}] job {job_id} failed, requeued: {e}", flush=True)
        else:
            r.rpush(DEAD, raw)
            if job is not None:
                try:
                    job.status = JobStatus.FAILED
                    job.error_message = str(e)[:500]
                    db.commit()
                except Exception:
                    db.rollback()
            print(f"[worker:{WORKER_TYPE}] job {job_id} dead-lettered: {e}", flush=True)
    finally:
        db.close()


def main():
    print(f"[worker:{WORKER_TYPE}] starting, polling {REDIS_URL}", flush=True)
    r = redis.from_url(REDIS_URL, decode_responses=True)
    from app.main import SessionLocal, Job, JobStatus, JobType

    _requeue_stale(r)
    idle = 0
    while True:
        try:
            # Reliable hand-off: the message sits in INFLIGHT until it reaches a
            # terminal state, so a crash here does not lose it.
            raw = r.brpoplpush(QUEUE, INFLIGHT, timeout=5)
            if not raw:
                # Nothing on the main queue - pick up anything parked for us.
                raw = r.rpoplpush(MINE, INFLIGHT)
                if not raw:
                    idle += 1
                    if idle % 60 == 0:
                        print(f"[worker:{WORKER_TYPE}] idle", flush=True)
                    continue
            idle = 0
            try:
                handle(raw, r, SessionLocal, Job, JobStatus, JobType)
            finally:
                _terminal(r, raw)

        except redis.ConnectionError:
            print("[worker] redis connection lost, retrying in 5s...", flush=True)
            time.sleep(5)
        except KeyboardInterrupt:
            print("[worker] shutting down", flush=True)
            break
        except Exception:
            # Never let an unexpected error kill the consumer loop.
            traceback.print_exc()
            time.sleep(1)


if __name__ == "__main__":
    main()
