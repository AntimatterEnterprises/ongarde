"""Shared constants for OnGarde.

All size limits and numeric caps used across modules are defined here.
No magic numbers in other modules — import from here.

Stories: E-001-S-005 (sizing constants), E-002 (INPUT_HARD_CAP, PRESIDIO_SYNC_CAP),
         E-003 (PRESIDIO_SYNC_CAP), E-004 (MAX_RESPONSE_BUFFER_BYTES streaming threshold)
         HOLD-002-fix (Adaptive Performance Protocol — calibration constants)
"""

# ─── Request / Response Size Limits (AC-E001-07) ─────────────────────────────

# Maximum allowed request body size.
# HTTP 413 is returned for bodies exceeding this limit, BEFORE any scan or
# upstream forwarding (story E-001-S-005, AC-E001-07).
MAX_REQUEST_BODY_BYTES: int = 1_048_576  # 1 MB = 1,048,576 bytes

# Response buffer threshold.
# Upstream responses whose Content-Length exceeds this value are routed to the
# streaming scan path instead of being buffered in memory.
# Responses ≤ this limit are fully buffered for scan (story E-001-S-005, AC-E001-07).
# Real streaming scan implemented in E-004; this story uses a pass-through stub.
MAX_RESPONSE_BUFFER_BYTES: int = 524_288  # 512 KB = 524,288 bytes

# ─── Scanner Size Constants (AC-E002-*, AC-E003-*) ───────────────────────────

# Hard truncation cap applied to request text before scanning.
# Inputs longer than this are truncated via apply_input_cap() before regex/NLP scan.
# Prevents catastrophic backtracking on very long inputs (E-002).
INPUT_HARD_CAP: int = 8_192  # 8,192 characters

# Default Presidio sync-vs-advisory threshold (fallback if calibration fails).
# At startup, run_calibration() measures actual p99 latency on the current hardware
# and sets PRESIDIO_SYNC_CAP dynamically. This constant is the conservative fallback
# used only when calibration fails (never assume 2-vCPU performance).
# See architecture.md §13 "Adaptive Performance Protocol"; E-003 implements the pool.
DEFAULT_PRESIDIO_SYNC_CAP: int = 500  # 500 characters (conservative fallback)

# ─── Adaptive Performance Protocol — Calibration Constants (HOLD-002 fix) ────
# These constants drive the startup calibration system that measures actual hardware
# performance and sets PRESIDIO_SYNC_CAP + PRESIDIO_TIMEOUT_S dynamically.
# See architecture.md §13 for the full algorithm.

# Input sizes to probe during calibration (chars). Clean text, no PII.
# Ordered smallest → largest to short-circuit early on slow hardware.
PRESIDIO_CALIBRATION_SIZES: tuple[int, ...] = (200, 500, 1000)

# Iterations per size during calibration. 5 calls × 3 sizes = 15 total.
# Small enough to complete in < 3 seconds; enough for a stable p99 estimate.
PRESIDIO_CALIBRATION_ITERATIONS: int = 5

# p99 latency target (ms). Sizes where measured p99 ≤ this threshold get sync treatment.
# 30ms gives 25% headroom below the 40ms hard timeout budget.
PRESIDIO_TARGET_LATENCY_MS: float = 30.0

# Multiplier applied to measured p99 to derive PRESIDIO_TIMEOUT_S.
# 1.5× provides a 50% safety buffer above the measured worst case.
PRESIDIO_TIMEOUT_MULTIPLIER: float = 1.5

# Minimum and maximum bounds for the derived timeout (seconds).
PRESIDIO_TIMEOUT_MIN_S: float = 0.025   # 25ms floor — never go below
PRESIDIO_TIMEOUT_MAX_S: float = 0.060   # 60ms ceiling — matches SCANNER_GLOBAL_TIMEOUT_S

# Conservative fallback timeout when calibration fails (seconds).
PRESIDIO_TIMEOUT_FALLBACK_S: float = 0.060  # 60ms — most conservative possible
