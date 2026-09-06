"""Local AI pipeline services.

Phase 1 ships the *interfaces* only. Every service raises a clear
``NotInPhase1Error`` instead of faking AI output. Later phases plug real
local, offline models behind these same classes without touching the API.
"""
