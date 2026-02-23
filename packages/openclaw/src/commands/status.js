'use strict';
/**
 * `npx @ongarde/openclaw status` command.
 * E-007-S-005: show current proxy status, key info, scanner tier.
 */

const { collectStatus, getExitCode } = require('../lib/status');
const { renderStatus } = require('../lib/display');

/**
 * Run the status command.
 *
 * @param {object} options
 * @param {number} [options.port=4242]
 * @param {boolean} [options.json=false] - Output JSON instead of text
 * @returns {Promise<{ exitCode: number, status: object }>}
 */
async function runStatus({ port = 4242, json = false } = {}) {
  const status = await collectStatus(port);

  if (json) {
    // Machine-readable JSON output
    const output = {
      proxy: status.proxyRunning ? 'running' : (status.stalePid ? 'stale-pid' : 'stopped'),
      pid: status.pid || null,
      port: status.port,
      scanner: status.health ? status.health.scanner : 'offline',
      scanner_mode: status.health ? status.health.scanner_mode : null,
      avg_scan_ms: status.health ? status.health.avg_scan_ms : null,
      queue_depth: status.health ? status.health.queue_depth : null,
      api_key_masked: status.apiKeyMasked || null,
      api_key_last_used: status.apiKeyLastUsed || null,
      requests_today: status.counters?.today?.requests ?? null,
      blocks_today: status.counters?.today?.blocks ?? null,
      dashboard: `http://localhost:${status.port}/dashboard`,
    };
    console.log(JSON.stringify(output, null, 2));
  } else {
    renderStatus(status);
  }

  const exitCode = getExitCode(status);
  return { exitCode, status };
}

module.exports = { runStatus };
