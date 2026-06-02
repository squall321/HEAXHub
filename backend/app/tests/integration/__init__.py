"""End-to-end integration tests (submission -> build -> publish -> run).

These tests run in-process with Celery in eager mode and require a reachable
PostgreSQL (the same DATABASE_URL used by the other DB-backed tests). They are
marked with the ``integration`` pytest marker so they are excluded from the
default suite — opt in with ``pytest -m integration``.
"""
