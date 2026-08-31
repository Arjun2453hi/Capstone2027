"""common — shared interfaces/contracts multiple stages depend on.

No stage should import from another stage's internals (the structural
issue the old build had). Everything meant for cross-stage reuse lives
here instead.
"""
