'use strict';
/**
 * Process management utilities for OnGarde proxy lifecycle.
 * E-007-S-002: spawn proxy, detect Python, manage PID.
 */

const { execSync, spawn, spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');
const os = require('os');
const { writePid, getLogPath, isPidActive } = require('./backup');

/**
 * Find the Python 3 executable.
 * Tries python3 first, then python.
 * @returns {string} Python executable path
 * @throws {Error} If Python 3 not found
 */
function findPython() {
  for (const cmd of ['python3', 'python']) {
    try {
      const result = spawnSync(cmd, ['--version'], { encoding: 'utf8', stdio: 'pipe' });
      if (result.stdout && result.stdout.includes('Python 3')) return cmd;
      if (result.stderr && result.stderr.includes('Python 3')) return cmd;
    } catch {}
  }
  throw new Error(
    'Python 3 not found. Install Python 3.12+ to use OnGarde.\n' +
    'Visit: https://www.python.org/downloads/'
  );
}

/**
 * Find the OnGarde Python package directory.
 * Looks for the ongarde package installation location.
 * @returns {string} Path to the ongarde package root
 */
function findOnGardePackageDir() {
  // Check ONGARDE_DIR env var first (for development and CI)
  if (process.env.ONGARDE_DIR && fs.existsSync(process.env.ONGARDE_DIR)) {
    return process.env.ONGARDE_DIR;
  }

  const python = findPython();
  try {
    const result = spawnSync(
      python,
      ['-c', 'import importlib.util, os; spec=importlib.util.find_spec("app.main"); print(os.path.dirname(os.path.dirname(spec.origin))) if spec else print("")'],
      { encoding: 'utf8', stdio: 'pipe' }
    );
    const pkgDir = (result.stdout || '').trim();
    if (pkgDir && fs.existsSync(path.join(pkgDir, 'app', 'main.py'))) {
      return pkgDir;
    }
  } catch {}

  throw new Error(
    'OnGarde Python package not found. Install it first:\n' +
    '  pip install ongarde[full]\n' +
    'Or set ONGARDE_DIR to the package directory.'
  );
}

/**
 * Spawn the OnGarde proxy as a detached background process.
 *
 * @param {object} options
 * @param {number} options.port - Port to listen on (default 4242)
 * @param {string} [options.configPath] - ONGARDE_CONFIG env var value
 * @param {string} [options.logPath] - Log file path (default ~/.ongarde/proxy.log)
 * @returns {{ pid: number, process: ChildProcess }}
 */
function spawnProxy({ port = 4242, configPath = null, logPath = null } = {}) {
  const python = findPython();
  const pkgDir = findOnGardePackageDir();
  const actualLogPath = logPath || getLogPath();

  // Ensure log file directory exists
  const logDir = path.dirname(actualLogPath);
  if (!fs.existsSync(logDir)) fs.mkdirSync(logDir, { recursive: true });

  const logStream = fs.openSync(actualLogPath, 'a');

  const env = { ...process.env };
  if (configPath) env.ONGARDE_CONFIG = configPath;

  const args = [
    '-m', 'uvicorn', 'app.main:app',
    '--host', '127.0.0.1',
    '--port', String(port),
    '--limit-concurrency', '100',
    '--backlog', '50',
    '--timeout-keep-alive', '5',
  ];

  const child = spawn(python, args, {
    cwd: pkgDir,
    env,
    stdio: ['ignore', logStream, logStream],
    detached: true,
  });

  // Write PID before detaching
  writePid(child.pid);

  // NOTE: Do NOT call unref() here — the caller (start.js) needs to listen
  // to exit events during the health polling window. The caller calls unref()
  // after health polling completes.

  return { pid: child.pid, process: child, logStream };
}

/**
 * Kill a process by PID.
 * Cross-platform: SIGTERM on Unix, taskkill on Windows.
 * If process doesn't die after timeoutMs, sends SIGKILL (Unix only).
 *
 * @param {number} pid
 * @param {object} [options]
 * @param {number} [options.timeoutMs=5000] - Grace period before SIGKILL
 * @param {boolean} [options.force=false] - Skip SIGTERM, go straight to SIGKILL/force
 * @returns {Promise<boolean>} true if process killed/already gone
 */
async function killProcess(pid, { timeoutMs = 5000, force = false } = {}) {
  if (!pid || !isPidActive(pid)) return true;

  if (process.platform === 'win32') {
    try {
      execSync(`taskkill /PID ${pid} /F`, { stdio: 'pipe' });
    } catch {}
    return !isPidActive(pid);
  }

  // Unix: SIGTERM first, then SIGKILL
  const signal = force ? 'SIGKILL' : 'SIGTERM';
  try { process.kill(pid, signal); } catch {}

  if (force) return !isPidActive(pid);

  // Wait up to timeoutMs for process to die
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    await sleep(100);
    if (!isPidActive(pid)) return true;
  }

  // Process still alive — SIGKILL
  try { process.kill(pid, 'SIGKILL'); } catch {}
  await sleep(500);
  return !isPidActive(pid);
}

/**
 * Find all OnGarde proxy processes (for uninstall).
 * @returns {number[]} Array of PIDs
 */
function findOnGardeProcesses() {
  if (process.platform === 'win32') {
    try {
      const out = execSync('tasklist /FI "IMAGENAME eq python*" /FO CSV', {
        encoding: 'utf8', stdio: 'pipe'
      });
      // Parse and filter for ongarde
      const pids = [];
      for (const line of out.split('\n')) {
        if (line.toLowerCase().includes('python')) {
          const match = line.match(/"(\d+)"/);
          if (match) pids.push(parseInt(match[1], 10));
        }
      }
      return pids;
    } catch { return []; }
  }

  try {
    const result = spawnSync('pgrep', ['-f', 'uvicorn app.main'], {
      encoding: 'utf8', stdio: 'pipe'
    });
    return (result.stdout || '').trim().split('\n')
      .filter(Boolean)
      .map(Number)
      .filter(n => !isNaN(n));
  } catch { return []; }
}

/**
 * Run pip uninstall ongarde.
 * @returns {boolean} true if successful
 */
function pipUninstall() {
  const python = findPython();
  try {
    const result = spawnSync(python, ['-m', 'pip', 'uninstall', 'ongarde', '-y'], {
      encoding: 'utf8', stdio: 'inherit',
    });
    return (result.status || 0) === 0;
  } catch {
    return false;
  }
}

function sleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

module.exports = {
  findPython,
  findOnGardePackageDir,
  spawnProxy,
  killProcess,
  findOnGardeProcesses,
  pipUninstall,
};
