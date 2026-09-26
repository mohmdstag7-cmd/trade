"""Observability layer (SPEC E3): logging, context, masking, crash, watchdog.

- :mod:`.logger` — structured per-category JSONL logging with rotation,
  retention, size cap, runtime levels and a debug window.
- :mod:`.context` — session id and trace ids (ContextVar-based).
- :mod:`.masking` — the redaction filter applied to every record.
- :mod:`.crash_handler` — sys/threading/Qt hooks writing crash reports.
- :mod:`.watchdog` — worker heartbeats and freeze detection.

The layer is Qt-free (PySide6 is imported lazily and optionally only for
the Qt message hook and the crash dialog).
"""
