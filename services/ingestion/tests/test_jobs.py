import asyncio

import pytest

from app import jobs


def test_the_queue_refuses_beyond_its_limit(monkeypatch):
    monkeypatch.setattr(jobs, "MAX_WAITING", 2)
    manager = jobs.JobManager()
    for n in range(2):
        manager._jobs[f"j{n}"] = jobs.Job(job_id=f"j{n}", doc_id="d", bucket="documents", key=f"{n}.pdf")
    with pytest.raises(jobs.QueueFull):
        asyncio.run(manager.submit("documents", "more.pdf", "d3", {}))
    manager._jobs["j0"].status = "running"  # a worker took one: there is room again
    monkeypatch.setattr(manager, "_run", lambda job: asyncio.sleep(0))
    assert asyncio.run(manager.submit("documents", "more.pdf", "d3", {})).status == "queued"
