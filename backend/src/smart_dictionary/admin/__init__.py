"""The owner's console, served to the LMS through the admin API (design §5.5).

Reads are aggregates and content, never a student's identity; writes are limited to the
settings in core.tunables, and every one is audited.
"""
