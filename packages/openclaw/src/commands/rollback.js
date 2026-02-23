'use strict';
/**
 * `npx @ongarde/openclaw rollback` command.
 * E-007-S-004: stop proxy, restore original baseUrl from backup.
 */

const fs = require('fs');
const { readBackup, readPid, deletePid, isPidActive } = require('../lib/backup');
const { killProcess } = require('../lib/process-utils');
const { findOpenClawConfig, updateBaseUrl, writeOpenClawConfig, extractBaseUrl } = require('../lib/openclaw-config');
const { ok, err, blank, info } = require('../lib/display');

/**
 * Run rollback: stop proxy + restore original config.
 *
 * @returns {Promise<{ success: boolean, error?: string }>}
 */
async function runRollback() {
  const backup = readBackup();
  if (!backup) {
    err('No backup found. OnGarde may not have been initialized.');
    info('Run: npx @ongarde/openclaw init');
    return { success: false, error: 'No backup found' };
  }

  // Step 1: Stop proxy
  const pid = readPid();
  if (pid && isPidActive(pid)) {
    const killed = await killProcess(pid, { timeoutMs: 5000 });
    if (killed) {
      ok('Proxy stopped');
      deletePid();
    } else {
      err(`Could not stop proxy (PID ${pid}). Kill it manually.`);
    }
  } else {
    // Proxy not running — still proceed with config restore
    if (pid) deletePid(); // clean up stale PID
    info('Proxy was not running');
  }

  // Step 2: Restore baseUrl
  const configPath = backup.openclaw_config_path;
  const originalBaseUrl = backup.original_base_url;

  if (!configPath || !originalBaseUrl) {
    err('Backup file is incomplete. Cannot restore config.');
    return { success: false, error: 'Incomplete backup' };
  }

  if (!fs.existsSync(configPath)) {
    err(`OpenClaw config not found at: ${configPath}`);
    err('You may need to restore it manually.');
    return { success: false, error: 'Config file not found' };
  }

  let config;
  try {
    config = JSON.parse(fs.readFileSync(configPath, 'utf8'));
  } catch (e) {
    err(`Failed to read config: ${e.message}`);
    return { success: false, error: 'Config read failed' };
  }

  // Find the baseUrl location and update
  let location, providerIndex;
  try {
    const extracted = extractBaseUrl(config);
    location = extracted.location;
    providerIndex = extracted.providerIndex;
  } catch {
    // If can't find baseUrl location, try to detect from backup
    location = 'root';
    providerIndex = -1;
  }

  updateBaseUrl(config, originalBaseUrl, location, providerIndex);

  try {
    writeOpenClawConfig(configPath, config);
  } catch (e) {
    err(`Failed to write config: ${e.message}`);
    return { success: false, error: 'Config write failed' };
  }

  // Step 3: Verify restored value
  let verifyConfig;
  try {
    verifyConfig = JSON.parse(fs.readFileSync(configPath, 'utf8'));
    const extracted = extractBaseUrl(verifyConfig);
    if (extracted.baseUrl !== originalBaseUrl) {
      err(`Verification failed: baseUrl is '${extracted.baseUrl}' but expected '${originalBaseUrl}'`);
      return { success: false, error: 'Verification failed' };
    }
  } catch (e) {
    err(`Verification failed: ${e.message}`);
    return { success: false, error: 'Verification failed' };
  }

  ok(`baseUrl restored → ${originalBaseUrl}`);
  ok('Rollback complete. OnGarde has been removed from your OpenClaw config.');
  blank();
  info('Note: Audit logs and key database preserved at ~/.ongarde/');
  info('Re-run: npx @ongarde/openclaw init  to set up OnGarde again.');

  return { success: true };
}

module.exports = { runRollback };
