'use strict';
/**
 * Backup and state file management for OnGarde CLI.
 * E-007-S-004: atomic backup before config modification.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

/**
 * Get the ~/.ongarde/ directory path (or ONGARDE_HOME override for tests).
 */
function getOnGardeHome() {
  return process.env.ONGARDE_HOME || path.join(os.homedir(), '.ongarde');
}

/**
 * Ensure the ~/.ongarde/ directory exists with correct permissions.
 */
function ensureOnGardeHome() {
  const home = getOnGardeHome();
  if (!fs.existsSync(home)) {
    fs.mkdirSync(home, { recursive: true });
    // Set 0700 on non-Windows
    if (process.platform !== 'win32') {
      try { fs.chmodSync(home, 0o700); } catch {}
    }
  }
  return home;
}

/**
 * Get path to the backup file.
 */
function getBackupPath() {
  return path.join(getOnGardeHome(), 'openclaw-backup.json');
}

/**
 * Get path to the state file (non-sensitive — masked key info).
 */
function getStatePath() {
  return path.join(getOnGardeHome(), 'state.json');
}

/**
 * Get path to PID file.
 */
function getPidPath() {
  return path.join(getOnGardeHome(), 'proxy.pid');
}

/**
 * Get path to proxy log file.
 */
function getLogPath() {
  return path.join(getOnGardeHome(), 'proxy.log');
}

/**
 * Check if a backup already exists.
 */
function backupExists() {
  return fs.existsSync(getBackupPath());
}

/**
 * Create the backup file atomically.
 * - Writes to a tmp file first, then renames (atomic on same filesystem).
 * - Sets permissions 0600.
 * - Does NOT overwrite if backup already exists (preserves original).
 *
 * @param {object} data - Backup data object
 * @throws {Error} If write fails
 */
function createBackup(data) {
  const backupPath = getBackupPath();

  // Never overwrite existing backup — preserve the original
  if (fs.existsSync(backupPath)) {
    return false; // not created (already exists)
  }

  ensureOnGardeHome();

  const content = JSON.stringify(
    { version: 1, created_at: new Date().toISOString(), ...data },
    null, 2
  );

  const tmpPath = backupPath + '.tmp';
  try {
    fs.writeFileSync(tmpPath, content, 'utf8');
    if (process.platform !== 'win32') {
      try { fs.chmodSync(tmpPath, 0o600); } catch {}
    }
    fs.renameSync(tmpPath, backupPath); // atomic
    return true;
  } catch (e) {
    // Clean up tmp on failure
    try { fs.unlinkSync(tmpPath); } catch {}
    throw e;
  }
}

/**
 * Read the backup file.
 * @returns {object|null} Backup data or null if not found
 */
function readBackup() {
  const backupPath = getBackupPath();
  if (!fs.existsSync(backupPath)) return null;
  try {
    return JSON.parse(fs.readFileSync(backupPath, 'utf8'));
  } catch {
    return null;
  }
}

/**
 * Write the state file (non-sensitive info — masked key, port, etc.)
 *
 * @param {object} data - State data
 */
function writeState(data) {
  ensureOnGardeHome();
  const statePath = getStatePath();
  const content = JSON.stringify(
    { version: 1, ...data },
    null, 2
  );
  const tmpPath = statePath + '.tmp';
  fs.writeFileSync(tmpPath, content, 'utf8');
  if (process.platform !== 'win32') {
    try { fs.chmodSync(tmpPath, 0o600); } catch {}
  }
  fs.renameSync(tmpPath, statePath);
}

/**
 * Read the state file.
 * @returns {object|null} State data or null if not found
 */
function readState() {
  const statePath = getStatePath();
  if (!fs.existsSync(statePath)) return null;
  try {
    return JSON.parse(fs.readFileSync(statePath, 'utf8'));
  } catch {
    return null;
  }
}

/**
 * Write the PID file.
 * @param {number} pid
 */
function writePid(pid) {
  ensureOnGardeHome();
  fs.writeFileSync(getPidPath(), String(pid) + '\n', 'utf8');
}

/**
 * Read the PID file.
 * @returns {number|null}
 */
function readPid() {
  const pidPath = getPidPath();
  if (!fs.existsSync(pidPath)) return null;
  try {
    const content = fs.readFileSync(pidPath, 'utf8').trim();
    const pid = parseInt(content, 10);
    return isNaN(pid) ? null : pid;
  } catch {
    return null;
  }
}

/**
 * Delete the PID file.
 */
function deletePid() {
  try { fs.unlinkSync(getPidPath()); } catch {}
}

/**
 * Check if a process PID is currently active.
 * @param {number} pid
 * @returns {boolean}
 */
function isPidActive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0); // signal 0 = check existence only
    return true;
  } catch {
    return false; // ESRCH = no such process
  }
}

module.exports = {
  getOnGardeHome,
  ensureOnGardeHome,
  getBackupPath,
  getStatePath,
  getPidPath,
  getLogPath,
  backupExists,
  createBackup,
  readBackup,
  writeState,
  readState,
  writePid,
  readPid,
  deletePid,
  isPidActive,
};
