"""MakeMKV robot-mode integration.

Everything in this package except :mod:`runner` and :mod:`inspect` is pure:
it turns bytes of ``makemkvcon -r`` output into records, verdicts and argv
lists with no I/O, so it can be tested against captured transcripts offline.
"""
