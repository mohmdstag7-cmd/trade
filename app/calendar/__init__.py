"""Economic calendar package (SPEC C3.9).

Python cannot read the MT5 calendar API, so the app maintains its own
UTC event store fed by (a) manual entries / CSV import and (b) the bundled
``mql5/CalendarExporter.mq5`` EA that writes the same CSV format every
minute from a live terminal.
"""
