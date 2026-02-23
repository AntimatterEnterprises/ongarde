'use strict';
/**
 * `npx @ongarde/openclaw start` command.
 * E-007-S-002: spawn proxy, health poll exit gate.
 */

const { isPortInUse, validatePort } = require('../lib/port-check');
const { pollUntilReady } = require('../lib/health');
const { spawnProxy, findPython, findOnGardePackageDir } = require('../lib/process-utils');
const { readPid, isPidActive, deletePid } = require('../lib/backup');
const { ok, err, spin, detail, blank } = require('../lib/display');

/**
 * Run the start command.
 *
 * @param {object} options
 * @param {number} [options.port=4242]
 * @param {string} [options.config] - ONGARDE_CONFIG path
 * @param {boolean} [options.quiet=false] - Suppress non-critical output
 * @returns {Promise<{ success: boolean, pid?: number, error?: string }>}
 */
async function runStart({ port = 4242, config = null, quiet = false } = {}) {
  const log = quiet ? () => {} : x => x;

  // Validate port
  try { port = validatePort(port); } catch (e) {
    err(e.message);
    return { success: false, error: e.message };
  }

  // Check if already running
  const existingPid = readPid();
  if (existingPid && isPidActive(existingPid)) {
    ok(`OnGarde is already running (PID ${existingPid})`);
    return { success: true, pid: existingPid };
  }

  // Clean up stale PID file
  if (existingPid) deletePid();

  // Check port conflict BEFORE spawning
  const portInUse = await isPortInUse(port);
  if (portInUse) {
    err(`Port ${port} is already in use.`);
    blank();
    process.stdout.write(`  Run with a different port:\n`);
    process.stdout.write(`    npx @ongarde/openclaw start --port 8080\n`);
    blank();
    process.stdout.write(`  Or stop the process using port ${port}:\n`);
    if (process.platform === 'win32') {
      process.stdout.write(`    netstat -ano | findstr :${port}\n`);
    } else {
      process.stdout.write(`    lsof -ti:${port} | xargs kill -9\n`);
    }
    blank();
    return { success: false, error: `Port ${port} in use` };
  }

  // Verify Python and OnGarde package available
  try {
    findPython();
    findOnGardePackageDir();
  } catch (e) {
    err(e.message);
    return { success: false, error: e.message };
  }

  process.stdout.write(`  Starting proxy on port ${port}...\n`);
  spin(`Waiting for OnGarde to be ready...`);

  // Track if proxy exited early
  let proxyExitCode = null;
  let proxyStderr = '';

  const { pid, process: child } = spawnProxy({ port, configPath: config });

  // Monitor for early exit — keep child referenced until polling done
  child.on('exit', (code) => {
    proxyExitCode = code != null ? code : null;
  });

  // Poll until ready with abort on process crash
  let crashDetected = false;
  const result = await pollUntilReady({
    port,
    timeoutMs: 30000,
    intervalMs: 500,
    requestTimeoutMs: 2000,
    shouldAbort: () => {
      if (proxyExitCode !== null && proxyExitCode !== 0) {
        crashDetected = true;
        return true;
      }
      return false;
    },
  });

  // Detach child process — allow parent to exit independently
  try { child.unref(); } catch {}

  if (result.ready) {
    ok(`OnGarde ready. En Garde. 🤺`);
    return { success: true, pid };
  }

  if (crashDetected || proxyExitCode !== null) {
    err(`Proxy process exited unexpectedly (code: ${proxyExitCode}).`);
    detail(`Check logs: cat ${require('../lib/backup').getLogPath()}`);
    return { success: false, error: `Proxy exited with code ${proxyExitCode}` };
  }

  // Timeout
  err(`Timeout: OnGarde did not become ready within 30 seconds.`);
  detail(`Check logs: cat ${require('../lib/backup').getLogPath()}`);
  return { success: false, error: 'timeout' };
}

module.exports = { runStart };
