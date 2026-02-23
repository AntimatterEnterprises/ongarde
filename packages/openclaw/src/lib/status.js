'use strict';
/**
 * Status data collection for npx ongarde status.
 * E-007-S-005.
 */

const { readPid, isPidActive, readState } = require('./backup');
const { checkHealth, checkScannerHealth, fetchWithTimeout } = require('./health');

/**
 * Collect all status information.
 *
 * @param {number} port - Proxy port (default 4242)
 * @returns {Promise<object>} Status object with all fields
 */
async function collectStatus(port = 4242) {
  const pid = readPid();
  const state = readState();

  // Step 1: Determine proxy running state
  let proxyRunning = false;
  let stalePid = false;

  if (pid !== null) {
    if (isPidActive(pid)) {
      // PID is active — verify with health check
      const health = await checkHealth(port, 2000);
      proxyRunning = health.ok;
      if (!health.ok && !proxyRunning) {
        // PID active but health check failed — might be starting
        proxyRunning = false;
      }
    } else {
      stalePid = true;
    }
  }

  // Step 2: Fetch health data if running
  let health = null;
  let entitySet = null;
  if (proxyRunning) {
    const healthResult = await checkHealth(port, 2000);
    health = healthResult.body;
    const scannerResult = await checkScannerHealth(port, 2000);
    entitySet = scannerResult ? scannerResult.entity_set : null;
  }

  // Step 3: Determine API key display
  let apiKeyMasked = null;
  let apiKeyLastUsed = null;
  let apiKeyId = null;

  if (state) {
    apiKeyMasked = state.api_key_masked || null;
    apiKeyId = state.api_key_id || null;
  }

  // If running, try to get key info from the proxy API
  if (proxyRunning && apiKeyId) {
    try {
      const res = await fetchWithTimeout(`http://localhost:${port}/dashboard/api/keys`, 2000);
      if (res.status === 200) {
        const body = await res.json();
        const keys = body.keys || [];
        const key = keys.find(k => k.id === apiKeyId || k.masked_key === apiKeyMasked);
        if (key) {
          apiKeyMasked = key.masked_key || apiKeyMasked;
          apiKeyLastUsed = key.last_used_at || null;
        }
      }
    } catch {
      // Graceful degradation — use state.json data
    }
  }

  // Step 4: Request/block counts (if running)
  let counters = null;
  if (proxyRunning) {
    try {
      const res = await fetchWithTimeout(`http://localhost:${port}/dashboard/api/counters`, 2000);
      if (res.status === 200) {
        counters = await res.json();
      }
    } catch {
      // Graceful degradation
    }
  }

  return {
    proxyRunning,
    stalePid,
    pid: proxyRunning ? pid : null,
    port,
    health,
    entitySet,
    apiKeyMasked,
    apiKeyLastUsed,
    apiKeyId,
    counters,
    ongardePath: state ? state.openclaw_config_path : null,
  };
}

/**
 * Determine exit code from status.
 * 0 = running+healthy, 1 = stopped, 2 = running+degraded
 */
function getExitCode(status) {
  if (!status.proxyRunning) return 1;
  if (status.health && (status.health.scanner === 'error' || status.health.scanner === 'degraded')) {
    return 2;
  }
  return 0;
}

module.exports = { collectStatus, getExitCode };
