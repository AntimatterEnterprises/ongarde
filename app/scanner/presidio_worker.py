"""Presidio NLP worker functions — E-003-S-001, E-003-S-002.

All functions in this module execute in the ProcessPoolExecutor worker process,
NOT in the main asyncio event loop. They are synchronous (no async/await).

The initializer= pattern ensures presidio_worker_init() is called ONCE per worker
at process startup. All subsequent presidio_scan_worker() calls in that process
reuse the pre-loaded model.

NEVER import from this module in async code for direct invocation — these are
worker-side functions only. The main process communicates with them via
ProcessPoolExecutor.submit() / run_in_executor().

Architecture reference: architecture.md §3, §13
Stories: E-003-S-001 (init + warmup), E-003-S-002 (scan worker + serializable return)

IMPORT RULES:
  - ``import re2`` where regex is needed — ``import re`` is PROHIBITED.
  - CI lint gate: grep -r "^import re$|^from re import|^import re " app/scanner/
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Module-level worker state ──────────────────────────────────────────────────
# Initialized to None at import time; set by presidio_worker_init() in the worker
# process. These variables are INVISIBLE to the main event-loop process.
# ProcessPoolExecutor creates a separate OS process — each worker has its own copy.

_nlp = None           # spaCy language model (en_core_web_sm)
_analyzer = None      # Presidio AnalyzerEngine
_entity_set: tuple[str, ...] = ()

# ── Warmup configuration (non-negotiable — eliminates JIT cold-start) ─────────
_WARMUP_SIZES: list[int] = [100, 200, 300, 500, 1_000]
_WARMUP_ITERATIONS: int = 3
_WARMUP_TEMPLATE: str = (
    "The quick brown fox jumps over the lazy dog. "
    "Alice went to the market to buy fresh vegetables and fruits. "
    "Bob called his colleague to discuss the quarterly report. "
    "The conference is scheduled for next Tuesday in the main meeting room. "
    "Please review the attached document and provide feedback by Friday. "
)


# ── Helper Functions ──────────────────────────────────────────────────────────


def _make_warmup_text(size: int) -> str:
    """Generate clean warmup text of exactly ``size`` characters.

    Contains no PII, no credentials, no dangerous patterns — purely prose
    to exercise the NLP pipeline without triggering any detection rules.
    Mirrors the pattern from calibration.py's _make_calibration_text().

    Args:
        size: Target character count.

    Returns:
        String of exactly ``size`` characters.
    """
    template = _WARMUP_TEMPLATE
    repetitions = (size // len(template)) + 2
    return (template * repetitions)[:size]


def _build_analyzer(entity_set: tuple[str, ...]) -> tuple:
    """Build NLP engine + AnalyzerEngine. Returns (nlp, analyzer).

    Separated from presidio_worker_init() for testability.

    Uses NlpEngineProvider to create a SpaCy-backed NLP engine, then
    wires in a US-only PhoneRecognizer. The generic global PhoneRecognizer
    is removed from the registry to prevent UK/international false positives.

    Args:
        entity_set: Entity types to detect (stored but not used here — used at scan time).

    Returns:
        Tuple of (nlp_engine, analyzer_engine).
    """
    import spacy
    from presidio_analyzer import AnalyzerEngine, RecognizerRegistry
    from presidio_analyzer.nlp_engine import NlpEngineProvider
    from presidio_analyzer.predefined_recognizers import PhoneRecognizer

    # Load spaCy model
    nlp = spacy.load("en_core_web_sm")

    # Build NLP engine via provider (recommended approach for Presidio 2.x)
    provider = NlpEngineProvider(
        nlp_configuration={
            "nlp_engine_name": "spacy",
            "models": [{"lang_code": "en", "model_name": "en_core_web_sm"}],
        }
    )
    nlp_engine = provider.create_engine()

    # Build custom registry — US-only phone detection
    registry = RecognizerRegistry()
    registry.load_predefined_recognizers()
    # Remove global PhoneRecognizer (covers all regions → too many false positives)
    registry.remove_recognizer("PhoneRecognizer")
    # Add US-only PhoneRecognizer
    us_phone = PhoneRecognizer(supported_regions=["US"])
    registry.add_recognizer(us_phone)

    analyzer = AnalyzerEngine(
        nlp_engine=nlp_engine,
        registry=registry,
        supported_languages=["en"],
    )

    return nlp, analyzer


def _run_warmup() -> None:
    """Run exactly 15 warmup scans (5 sizes × 3 iterations) to eliminate JIT cold-start.

    Directly calls _analyzer.analyze() — does NOT go through presidio_scan_worker()
    to keep warmup internal to init and avoid the uninitialized-worker guard.

    Any individual warmup failure is caught and logged as WARNING — a warmup
    failure does NOT prevent the worker from being usable.
    """
    global _analyzer, _entity_set
    total_scans = 0
    for size in _WARMUP_SIZES:
        text = _make_warmup_text(size)
        for i in range(_WARMUP_ITERATIONS):
            try:
                _analyzer.analyze(
                    text=text,
                    language="en",
                    entities=list(_entity_set) if _entity_set else ["CREDIT_CARD"],
                )
                total_scans += 1
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Warmup scan failed (non-fatal) size=%d iter=%d: %s",
                    size,
                    i,
                    exc,
                )

    logger.info(
        "Presidio worker init: complete (%d warmup scans done)", total_scans
    )


# ── Initializer (ProcessPoolExecutor initializer= — called ONCE per worker) ───


def presidio_worker_init(entity_set: tuple[str, ...]) -> None:
    """Worker process initializer — called ONCE at ProcessPoolExecutor startup.

    MUST be used as the ``initializer=`` argument to ``ProcessPoolExecutor``.
    NEVER call per-request — that would defeat the entire performance architecture.

    Sequence:
      1. Load en_core_web_sm via NlpEngineProvider
      2. Register US-only PhoneRecognizer (removes global one)
      3. Create AnalyzerEngine
      4. Store in module-level _nlp, _analyzer, _entity_set
      5. Run 15 warmup scans (5 sizes × 3 iterations) — non-negotiable

    Args:
        entity_set: Tuple of Presidio entity type names to detect.
                    Passed as ``initargs=(entity_set,)`` to ProcessPoolExecutor.

    Raises:
        Nothing — any exception is caught and logged. If the worker fails to
        initialize (e.g., spaCy model missing), _analyzer remains None and
        presidio_scan_worker() will raise RuntimeError on first call.
    """
    global _nlp, _analyzer, _entity_set

    logger.info("Presidio worker init: loading en_core_web_sm")
    _entity_set = entity_set

    try:
        _nlp, _analyzer = _build_analyzer(entity_set)
        logger.info(
            "Presidio worker init: model loaded, running %d warmup scans",
            len(_WARMUP_SIZES) * _WARMUP_ITERATIONS,
        )
        _run_warmup()

    except Exception as exc:  # noqa: BLE001
        logger.error(
            "Presidio worker init FAILED — worker will be unavailable: %s: %s",
            type(exc).__name__,
            exc,
        )
        # _analyzer remains None → presidio_scan_worker() raises RuntimeError


# ── Scan Worker (called via run_in_executor in the main event loop) ───────────


def presidio_scan_worker(text: str) -> list[dict]:
    """Scan text for PII entities using the pre-loaded Presidio AnalyzerEngine.

    Executes synchronously in the ProcessPoolExecutor worker process. Returns
    a list of serializable dicts — NOT RecognizerResult objects. This is required
    because Python's ProcessPoolExecutor passes return values via pickle, and
    Presidio RecognizerResult objects are not guaranteed to be pickle-safe across
    all versions.

    ALWAYS called via loop.run_in_executor(scan_pool, presidio_scan_worker, text)
    from the main async process. NEVER call directly in async code.

    Args:
        text: Input text to scan. Should already be capped at INPUT_HARD_CAP chars.

    Returns:
        List of entity dicts. Each dict:
            {
                "entity_type": str,   # e.g. "CREDIT_CARD", "US_SSN"
                "start": int,         # character offset in text
                "end": int,           # character offset in text
                "score": float,       # Presidio confidence [0.0, 1.0]
            }
        Empty list if no entities detected.

    Raises:
        RuntimeError: If presidio_worker_init() was not called before this function
                      (i.e., _analyzer is None). This propagates through run_in_executor
                      to scan_or_block(), which treats it as SCANNER_ERROR → BLOCK.
    """
    global _analyzer, _entity_set

    if _analyzer is None:
        raise RuntimeError(
            "Presidio worker not initialized — call presidio_worker_init() first"
        )

    results = _analyzer.analyze(
        text=text,
        language="en",
        entities=list(_entity_set) if _entity_set else [],
    )

    return [
        {
            "entity_type": r.entity_type,
            "start": r.start,
            "end": r.end,
            "score": float(r.score),  # Cast to float — avoids numpy/other scalar types
        }
        for r in results
    ]
