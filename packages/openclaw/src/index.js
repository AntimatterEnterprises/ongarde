'use strict';
/**
 * @ongarde/openclaw — Node.js API exports.
 * Provides programmatic access to the OnGarde CLI functionality.
 */

const { runInit } = require('./commands/init');
const { runStart } = require('./commands/start');
const { runStatus } = require('./commands/status');
const { runRollback } = require('./commands/rollback');
const { runUninstall } = require('./commands/uninstall');
const { pollUntilReady, checkHealth } = require('./lib/health');
const { isPortInUse, validatePort } = require('./lib/port-check');

module.exports = {
  runInit,
  runStart,
  runStatus,
  runRollback,
  runUninstall,
  pollUntilReady,
  checkHealth,
  isPortInUse,
  validatePort,
};
