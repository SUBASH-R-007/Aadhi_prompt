"""Durable DB-backed job system.

Modules (import them directly; this package stays import-light to avoid cycles):

* ``base``      contract: exceptions, ``JobContext`` protocol, handler registry
* ``queue``     enqueue / claim / fenced state transitions / cancel / retry / summaries
* ``context``   ``DBJobContext`` (non-blocking, buffered, lease-fenced)
* ``heartbeat`` one process-wide heartbeat thread for every lease held by this process
* ``worker``    ``Worker`` threads (one fresh event loop per job), reaper, restart recovery
* ``runner``    per-job event loop whose thread work is abandoned (never awaited) on cancel
* ``events``    JobEvent serialisation + SSE stream
* ``inline``    ``WORKER_MODE=inline`` helpers for the API process
* ``limits``    process-wide concurrency limits usable from any thread / event loop
* ``cleanup``   the ``cleanup`` job (retention + orphaned asset GC)
* ``lease``     worker ids (``host:pid:boot_uuid:thread``) and the fencing predicate
* ``util``      time / strict JSON / redaction / DB-error classification helpers
"""
