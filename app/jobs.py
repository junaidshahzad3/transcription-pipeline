"""A small job store and worker pool for long files.

Kept in memory so the project runs with no extra services. In production the same
interface maps to PostgreSQL (jobs table) + Celery/Redis (queue) as described in README.
"""
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone


class JobStore:
    def __init__(self, max_workers: int = 2):
        self._jobs: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max_workers)

    def create(self, filename: str) -> dict:
        job = {"id": str(uuid.uuid4()), "status": "queued", "filename": filename,
               "progress": {"done": 0, "total": 0}, "attempts": 0, "error": None,
               "result": None, "created_at": datetime.now(timezone.utc).isoformat(),
               "completed_at": None}
        with self._lock:
            self._jobs[job["id"]] = job
        return job

    def get(self, job_id: str) -> dict | None:
        with self._lock:
            j = self._jobs.get(job_id)
            return dict(j) if j else None

    def update(self, job_id: str, **fields):
        with self._lock:
            self._jobs[job_id].update(fields)

    def submit(self, job_id: str, fn, *args):
        """Run fn in the background and record success or failure on the job."""
        def runner():
            self.update(job_id, status="processing",
                        attempts=self._jobs[job_id]["attempts"] + 1)
            try:
                result = fn(*args, lambda d, t: self.update(job_id, progress={"done": d, "total": t}))
                self.update(job_id, status="completed", result=result, error=None,
                            completed_at=datetime.now(timezone.utc).isoformat())
            except Exception as e:  # recorded so the client sees a clear reason
                self.update(job_id, status="failed", error=str(e))
        self._pool.submit(runner)
