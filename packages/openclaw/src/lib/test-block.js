'use strict';
/**
 * Test block utilities for the aha moment step.
 * E-007-S-003: sends sk-ongarde-test-fake-key-12345 through proxy.
 */

const { fetchWithTimeout } = require('./health');

// The registered test credential from E-002 definitions.py
const TEST_CREDENTIAL = 'sk-ongarde-test-fake-key-12345';

/**
 * Send a test block request through the proxy.
 * Sends a chat completions request with the test credential in the body.
 *
 * @param {object} options
 * @param {number} options.port - Proxy port (default 4242)
 * @param {string} [options.apiKey] - OnGarde API key (optional — auth bypassed by default)
 * @param {number} [options.timeoutMs=5000] - Request timeout
 * @returns {Promise<{ blocked: boolean, ruleId?: string, riskLevel?: string, scanId?: string, test?: boolean, body?: object, status: number }>}
 */
async function sendTestBlock({ port = 4242, apiKey = null, timeoutMs = 5000 } = {}) {
  const url = `http://localhost:${port}/v1/chat/completions`;

  const requestBody = {
    model: 'gpt-4',
    messages: [
      {
        role: 'user',
        content: `Testing OnGarde protection. ${TEST_CREDENTIAL}`,
      },
    ],
  };

  const headers = {
    'Content-Type': 'application/json',
  };

  if (apiKey) {
    headers['X-OnGarde-Key'] = apiKey;
  }

  let response;
  try {
    response = await fetchWithTimeout(url, timeoutMs, {
      method: 'POST',
      headers,
      body: JSON.stringify(requestBody),
    });
  } catch (err) {
    if (err.name === 'AbortError') {
      return { blocked: false, error: 'timeout', status: 0 };
    }
    return { blocked: false, error: err.message, status: 0 };
  }

  let body = null;
  try { body = await response.json(); } catch {}

  if (response.status === 400) {
    // Extract block info from response body
    const ongarde = body?.ongarde || body?.error?.detail?.ongarde || {};
    const scanId = response.headers.get('x-ongarde-scan-id') || ongarde.scan_id;

    return {
      blocked: true,
      ruleId: ongarde.rule_id || ongarde.reason || 'CREDENTIAL_DETECTED',
      riskLevel: ongarde.risk_level || 'CRITICAL',
      scanId,
      test: ongarde.test === true,
      body,
      status: 400,
    };
  }

  return {
    blocked: false,
    body,
    status: response.status,
  };
}

/**
 * Check if there are any audit events (for first-run detection).
 * Calls GET /dashboard/api/events?limit=1 via the proxy.
 *
 * @param {number} port
 * @param {string} [apiKey]
 * @returns {Promise<boolean>} true if this is the first run (no events)
 */
async function isFirstRun(port = 4242, apiKey = null) {
  const url = `http://localhost:${port}/dashboard/api/events?limit=1`;
  const headers = {};
  if (apiKey) headers['X-OnGarde-Key'] = apiKey;

  try {
    const response = await fetchWithTimeout(url, 3000, { headers });
    if (response.status === 200) {
      const body = await response.json();
      const events = body.events || body || [];
      return Array.isArray(events) && events.length === 0;
    }
    // If endpoint doesn't exist (404) or error, default to first run
    return response.status === 404;
  } catch {
    return true; // Can't check — assume first run
  }
}

module.exports = { sendTestBlock, isFirstRun, TEST_CREDENTIAL };
