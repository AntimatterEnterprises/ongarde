'use strict';
/**
 * `npx @ongarde/openclaw uninstall` command.
 * E-007-S-004, AC-E007-06: stop proxy, restore config, pip uninstall.
 */

const { runRollback } = require('./rollback');
const { findOnGardeProcesses, killProcess, pipUninstall } = require('../lib/process-utils');
const { isPidActive } = require('../lib/backup');
const { ok, err, blank, info } = require('../lib/display');

/**
 * Run uninstall: rollback + pip remove + verify no processes remain.
 *
 * @returns {Promise<{ success: boolean, error?: string }>}
 */
async function runUninstall() {
  // Step 1: Rollback (stop proxy + restore config)
  const rollbackResult = await runRollback();
  if (!rollbackResult.success && rollbackResult.error !== 'No backup found') {
    // If rollback failed for a reason other than "no backup", abort
    return { success: false, error: rollbackResult.error };
  }

  // Step 2: Remove Python package
  info('Removing OnGarde Python package...');
  const uninstalled = pipUninstall();
  if (uninstalled) {
    ok('Python package removed');
  } else {
    err('Could not remove Python package. Run manually: pip uninstall ongarde -y');
  }

  // Step 3: Kill any remaining OnGarde processes
  const remainingPids = findOnGardeProcesses();
  for (const pid of remainingPids) {
    if (isPidActive(pid)) {
      await killProcess(pid, { timeoutMs: 3000 });
    }
  }

  const stillRunning = findOnGardeProcesses().filter(pid => isPidActive(pid));
  if (stillRunning.length > 0) {
    err(`Some processes still running: ${stillRunning.join(', ')}`);
    err('Kill them manually: kill -9 ' + stillRunning.join(' '));
  }

  blank();
  ok('OnGarde fully uninstalled.');
  blank();
  info('Note: Audit logs preserved at ~/.ongarde/audit.db');
  info('To completely remove all data: rm -rf ~/.ongarde/');

  return { success: true };
}

module.exports = { runUninstall };
