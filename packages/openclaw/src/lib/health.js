'use strict';
/**
 * Health polling for OnGarde proxy.
 * E-007-S-002: polls /health until HTTP 200 or timeout.
 *
 * Per packages/openclaw/README.md exit gate contract:
 *   - Polls every 500ms
 *   - Per-request timeout: 2000ms
 *   - Total deadline: 30000ms (30 seconds)
 *   - HTTP 200 = ready (exit 0)
 *   - HTTP 503 = still starting (continue)
 *   - Network error = continue (proxy not yet listening)
 *   - Timeout = exit 1
 */

/**
 * Fetch with a timeout.
 * Uses built-in fetch (Node 18+) with AbortController.
 *
 * @param {string} url
 * @param {number} timeoutMs
 * @param {object} [options] - Additional fetch options (method, headers, body, etc.)
 * @returns {Promise<Response>}
 */
async function fetchWithTimeout(url, timeoutMs, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(url, { ...options, signal: controller.signal });
    clearTimeout(timer);
    return response;
  } catch (err) {
    clearTimeout(timer);
    throw err;
  }
}

/**
 * Poll the /health endpoint until ready or timeout.
 *
 * @param {object} options
 * @param {number} options.port - Proxy port (default 4242)
 * @param {number} [options.timeoutMs=30000] - Total polling timeout in ms
 * @param {number} [options.intervalMs=500] - Polling interval in ms
 * @param {number} [options.requestTimeoutMs=2000] - Per-request timeout in ms
 * @param {Function} [options.onPoll] - Called on each poll attempt (for logging)
 * @param {Function} [options.shouldAbort] - Called before each poll; if returns true, abort
 * @returns {Promise<{ ready: boolean, reason?: string, response?: object }>}
 */
async function pollUntilReady({
  port = 4242,
  timeoutMs = 30000,
  intervalMs = 500,
  requestTimeoutMs = 2000,
  onPoll = null,
  shouldAbort = null,
} = {}) {
  const url = `http://localhost:${port}/health`;
  const deadline = Date.now() + timeoutMs;

  while (Date.now() < deadline) {
    // Check abort signal
    if (shouldAbort && shouldAbort()) {
      return { ready: false, reason: 'aborted' };
    }

    if (onPoll) onPoll({ url, elapsed: Date.now() - (deadline - timeoutMs) });

    try {
      const response = await fetchWithTimeout(url, requestTimeoutMs);
      if (response.status === 200) {
        let body = null;
        try { body = await response.json(); } catch {}
        return { ready: true, response: body };
      }
      // 503 = starting, other = retry
    } catch {
      // Network error — proxy not yet listening, continue
    }

    // Wait before next poll (but respect deadline)
    const remaining = deadline - Date.now();
    if (remaining <= 0) break;
    await sleep(Math.min(intervalMs, remaining));
  }

  return { ready: false, reason: 'timeout' };
}

/**
 * Single health check (not a polling loop).
 * Used by the status command.
 *
 * @param {number} port
 * @param {number} [timeoutMs=2000]
 * @returns {Promise<{ ok: boolean, status?: number, body?: object }>}
 */
async function checkHealth(port = 4242, timeoutMs = 2000) {
  const url = `http://localhost:${port}/health`;
  try {
    const response = await fetchWithTimeout(url, timeoutMs);
    let body = null;
    try { body = await response.json(); } catch {}
    return { ok: response.status === 200, status: response.status, body };
  } catch {
    return { ok: false, status: null, body: null };
  }
}

/**
 * Single scanner health check.
 *
 * @param {number} port
 * @param {number} [timeoutMs=2000]
 * @returns {Promise<object|null>}
 */
async function checkScannerHealth(port = 4242, timeoutMs = 2000) {
  const url = `http://localhost:${port}/health/scanner`;
  try {
    const response = await fetchWithTimeout(url, timeoutMs);
    if (response.status === 200) {
      return await response.json();
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Sleep for a given number of milliseconds.
 */
function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

module.exports = { pollUntilReady, checkHealth, checkScannerHealth, sleep, fetchWithTimeout };
