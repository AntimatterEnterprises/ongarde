"""OnGarde models package.

Defines the shared data contracts used across the scan pipeline and proxy handler:

  - scan.py    — ScanResult, Action, RiskLevel (scan gate contracts for E-002/E-003)
  - block.py   — Response builders for HTTP 400 BLOCK and HTTP 502 upstream-unavailable

These models are the single source of truth for the scan gate API contract.
E-002 (regex scanner) and E-003 (Presidio NLP scanner) fulfil the ScanResult contract.
"""
