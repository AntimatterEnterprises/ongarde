'use strict';
/**
 * `npx @ongarde/openclaw init` — 4-step onboarding wizard.
 * E-007-S-001 through E-007-S-003.
 *
 * Steps:
 *   1. Install: detect OpenClaw config, backup, update baseUrl
 *   2. Start proxy (E-007-S-002)
 *   3. Verify: health check, show stats (E-007-S-003)
 *   4. Aha moment: guided test block (first run only, E-007-S-003)
 */

'use strict';

const os = require('os');
const readline = require('readline');
const { fetchWithTimeout } = require('../lib/health');
const {
  findOpenClawConfig,
  extractBaseUrl,
  updateBaseUrl,
  writeOpenClawConfig,
} = require('../lib/openclaw-config');
const {
  backupExists,
  createBackup,
  writeState,
  getOnGardeHome,
  ensureOnGardeHome,
} = require('../lib/backup');
const { isPortInUse, validatePort } = require('../lib/port-check');
const { runStart } = require('./start');
const { checkHealth, checkScannerHealth } = require('../lib/health');
const { sendTestBlock, isFirstRun } = require('../lib/test-block');
const {
  printBrandHeader,
  ok, err, spin, detail, warn, info, blank, separator, renderAhaMoment,
} = require('../lib/display');

/**
 * Prompt the user for input (yes/no style).
 * Returns true for Y/y/Enter, false for N/n.
 *
 * @param {string} question
 * @returns {Promise<boolean>}
 */
function promptYesNo(question) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    rl.question(question, (answer) => {
      rl.close();
      const a = answer.trim().toLowerCase();
      resolve(a === '' || a === 'y' || a === 'yes');
    });
  });
}

/**
 * Prompt user to press Enter to continue.
 */
function promptContinue(message) {
  return new Promise((resolve) => {
    const rl = readline.createInterface({
      input: process.stdin,
      output: process.stdout,
    });
    rl.question(message, () => {
      rl.close();
      resolve();
    });
  });
}

/**
 * Create the API key via the proxy.
 * Calls POST /dashboard/api/keys — auth bypassed by default.
 *
 * @param {number} port
 * @returns {Promise<{ plaintext: string, masked: string, keyId: string }|null>}
 */
async function createApiKey(port) {
  const url = `http://localhost:${port}/dashboard/api/keys`;
  try {
    const response = await fetchWithTimeout(url, 5000, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user_id: 'default' }),
    });
    if (response.status === 200 || response.status === 201) {
      const body = await response.json();
      const plaintext = body.key || body.plaintext_key || body.api_key;
      if (plaintext && plaintext.startsWith('ong-')) {
        const masked = body.masked_key || `ong-...${plaintext.slice(-4)}`;
        const keyId = body.id || plaintext.slice(4); // raw ULID
        return { plaintext, masked, keyId };
      }
    }
    return null;
  } catch {
    return null;
  }
}

/**
 * Detect system RAM and return bytes.
 */
function getTotalRamBytes() {
  return os.totalmem();
}

/**
 * Main init wizard.
 *
 * @param {object} options
 * @param {number} [options.port=4242]
 * @param {string} [options.config] - OpenClaw config path (--config flag)
 * @param {boolean} [options.yes=false] - Non-interactive mode
 * @returns {Promise<{ success: boolean, error?: string }>}
 */
async function runInit({ port = 4242, config: configOverride = null, yes = false } = {}) {
  // Validate port
  try { port = validatePort(port); } catch (e) {
    err(e.message);
    return { success: false, error: e.message };
  }

  // ─── Show brand header on fresh install ────────────────────────────────────
  if (!backupExists()) {
    printBrandHeader();
    blank();
  }

  info('Installing OnGarde proxy...');
  // Package is already installed (this is the CLI running from the package)
  ok('Proxy installed (v1.0.0)');
  blank();

  // ─── Step 1: Configure OpenClaw ────────────────────────────────────────────
  info('Configuring OpenClaw...');

  let openClawConfig, openClawConfigPath, originalBaseUrl, baseUrlLocation, baseUrlProviderIndex;

  try {
    const found = findOpenClawConfig(configOverride);
    openClawConfigPath = found.path;
    openClawConfig = found.config;
    ok(`Config found: ${openClawConfigPath}`);
  } catch (e) {
    if (e.code === 'CONFIG_NOT_FOUND') {
      err(`Config not found at default path (~/.openclaw/config.json)`);
      blank();
      info('Specify your config path:');
      info(`  npx @ongarde/openclaw init --config /path/to/openclaw.json`);
      blank();
      info('Typical locations:');
      info('  ~/.openclaw/config.json');
      info('  ~/.config/openclaw/settings.json');
      info('  /etc/openclaw/config.json');
      blank();
      return { success: false, error: 'Config not found' };
    }
    err(e.message);
    return { success: false, error: e.message };
  }

  try {
    const extracted = extractBaseUrl(openClawConfig);
    originalBaseUrl = extracted.baseUrl;
    baseUrlLocation = extracted.location;
    baseUrlProviderIndex = extracted.providerIndex;
  } catch (e) {
    err(`Could not find baseUrl in config: ${e.message}`);
    err('Your config may have an unsupported format.');
    return { success: false, error: 'No baseUrl in config' };
  }

  // ─── Step 1a: Check RAM for Lite mode ──────────────────────────────────────
  const ramBytes = getTotalRamBytes();
  const twoGb = 2 * 1024 * 1024 * 1024;
  if (ramBytes < twoGb && !backupExists()) {
    const ramGb = (ramBytes / (1024 ** 3)).toFixed(1);
    blank();
    warn(`System RAM: ${ramGb} GB detected.`);
    blank();
    info('OnGarde Full mode requires ~1.5 GB RAM (Presidio NLP scanner).');
    info('Lite mode uses regex-only PII detection (~200 MB RAM).');
    blank();
    info('Lite mode trade-offs:');
    info('  • No Luhn validation for credit cards');
    info('  • No contextual PII detection (NLP disabled)');
    info('  • Not suitable for HIPAA/GDPR/PCI DSS workloads');
    blank();

    let useLite = false;
    if (yes || !process.stdout.isTTY) {
      // Non-interactive: default to Full mode (safer), log warning
      warn('Non-interactive mode: proceeding with Full mode despite low RAM.');
      warn('Pass --lite to explicitly enable Lite mode.');
    } else {
      useLite = !(await promptYesNo('  Enable Lite mode? [Y/n]: '));
      if (!useLite) {
        warn('Proceeding with Full mode. Performance may be affected on low-RAM systems.');
      }
    }

    if (useLite) {
      ok('Lite mode selected');
      // Set scanner.mode in OnGarde config (write to ~/.ongarde/config.yaml)
      ensureOnGardeHome();
      const ongardeCfgPath = require('path').join(getOnGardeHome(), 'config.yaml');
      const fs = require('fs');
      const ongardeConfig = fs.existsSync(ongardeCfgPath)
        ? fs.readFileSync(ongardeCfgPath, 'utf8')
        : 'version: 1\n';
      // Add or update scanner.mode
      const updated = ongardeConfig.includes('scanner:')
        ? ongardeConfig.replace(/scanner:[\s\S]*?(?=\n\S|\Z)/, 'scanner:\n  mode: lite\n')
        : ongardeConfig + '\nscanner:\n  mode: lite\n';
      fs.writeFileSync(ongardeCfgPath, updated, 'utf8');
    }
  }

  // ─── Step 1b: Backup + update baseUrl ─────────────────────────────────────
  const newBaseUrl = `http://localhost:${port}/v1`;

  // Backup BEFORE modification
  if (!backupExists()) {
    try {
      createBackup({
        openclaw_config_path: openClawConfigPath,
        original_base_url: originalBaseUrl,
        ongarde_port: port,
      });
    } catch (e) {
      err(`Failed to create backup: ${e.message}`);
      err('Cannot proceed without backup — your config has NOT been modified.');
      return { success: false, error: 'Backup failed' };
    }
  } else {
    info('Previous backup preserved at ~/.ongarde/openclaw-backup.json');
  }

  // Update config
  if (originalBaseUrl !== newBaseUrl) {
    updateBaseUrl(openClawConfig, newBaseUrl, baseUrlLocation, baseUrlProviderIndex);
    try {
      writeOpenClawConfig(openClawConfigPath, openClawConfig);
      ok(`baseUrl updated → ${newBaseUrl}`);
    } catch (e) {
      err(`Failed to update config: ${e.message}`);
      return { success: false, error: 'Config update failed' };
    }
  } else {
    ok(`baseUrl already set to ${newBaseUrl}`);
  }

  blank();

  // ─── Step 2: Start proxy ───────────────────────────────────────────────────
  info(`Starting proxy on port ${port}...`);

  const startResult = await runStart({ port, quiet: true });
  if (!startResult.success) {
    // runStart already printed the error
    return { success: false, error: startResult.error };
  }

  ok('Proxy started (127.0.0.1:' + port + ')');
  blank();

  // ─── Step 3: Verify health ─────────────────────────────────────────────────
  info('Verifying protection...');
  blank();

  const health = await checkHealth(port, 3000);
  const scannerHealth = await checkScannerHealth(port, 3000);

  if (health.ok) {
    ok('Health check passed');
    ok(`Intercepting LLM traffic on port ${port}`);
    const scannerStatus = health.body?.scanner || 'unknown';
    if (scannerStatus === 'healthy') {
      ok(`Scanner: healthy`);
    } else {
      warn(`Scanner: ${scannerStatus}`);
    }
    if (health.body) {
      const avgMs = health.body.avg_scan_ms;
      const qd = health.body.queue_depth;
      if (avgMs !== undefined) detail(`avg latency: ${avgMs.toFixed ? avgMs.toFixed(1) : avgMs}ms   queue depth: ${qd}`);
    }
    if (scannerHealth && scannerHealth.entity_set) {
      detail(`entity set: [${scannerHealth.entity_set.join(', ')}]`);
    }
    blank();
    ok(`Dashboard: http://localhost:${port}/dashboard`);
    blank();
    info(`Let's confirm your security is working.`);
    blank();
  } else {
    warn('Health check incomplete — proxy may still be starting.');
  }

  // ─── Step 3b: Create API key ────────────────────────────────────────────────
  let apiKeyPlaintext = null;
  let apiKeyMasked = null;
  let apiKeyId = null;

  const keyResult = await createApiKey(port);
  if (keyResult) {
    apiKeyPlaintext = keyResult.plaintext;
    apiKeyMasked = keyResult.masked;
    apiKeyId = keyResult.keyId;

    blank();
    ok('API key created (shown once — save it now):');
    blank();
    process.stdout.write(`      ${apiKeyPlaintext}\n`);
    blank();
    info('This key will not be shown again.');
    blank();

    // Save state (NOT plaintext)
    writeState({
      api_key_id: apiKeyId,
      api_key_masked: apiKeyMasked,
      openclaw_config_path: openClawConfigPath,
      ongarde_port: port,
      created_at: new Date().toISOString(),
    });

    // Update backup with key_id
    const backupPath = require('../lib/backup').getBackupPath();
    const fs = require('fs');
    try {
      const existingBackup = JSON.parse(fs.readFileSync(backupPath, 'utf8'));
      existingBackup.api_key_id = apiKeyId;
      fs.writeFileSync(backupPath, JSON.stringify(existingBackup, null, 2), 'utf8');
    } catch {}
  } else {
    warn('Could not create API key automatically.');
    info('Create one manually: npx @ongarde/openclaw status');
  }

  // ─── Step 4: Test block (mandatory on first run) ───────────────────────────
  const firstRun = await isFirstRun(port, apiKeyPlaintext);

  if (firstRun) {
    blank();
    separator('Test Block');
    blank();
    info('This sends a request containing a registered test credential.');
    info('OnGarde will block it. This is how you confirm protection is active.');
    blank();

    // Interactive mode: wait for Enter
    const isInteractive = process.stdout.isTTY && !yes;
    if (isInteractive) {
      info('This sends a registered test credential through the proxy.');
      info('OnGarde will block it — no real API call is made.');
      info('This step is required on first run.');
      blank();
      await promptContinue('  [Press Enter to run, or Ctrl+C to exit] ');
      blank();
    }

    const blockStart = Date.now();
    info(`Running test...`);
    detail(`POST http://localhost:${port}/v1/chat/completions [test credential]`);
    blank();

    const blockResult = await sendTestBlock({ port, apiKey: apiKeyPlaintext });
    const blockElapsed = Date.now() - blockStart;

    if (blockResult.blocked) {
      renderAhaMoment({
        ruleId: blockResult.ruleId || 'CREDENTIAL_DETECTED',
        riskLevel: blockResult.riskLevel || 'CRITICAL',
        scanId: blockResult.scanId || 'unknown',
      });
      blank();
      info('Your security layer is active. You\'re protected.');
      blank();
      info(`Dashboard: http://localhost:${port}/dashboard`);
      blank();

      if (blockElapsed > 2000) {
        warn(`Aha moment took ${(blockElapsed/1000).toFixed(1)}s (target: <2s)`);
      }
    } else if (blockResult.error === 'timeout') {
      err('Test request timed out (>5s). Check proxy status.');
      detail('Run: npx @ongarde/openclaw status');
      return { success: false, error: 'Test block timed out' };
    } else if (blockResult.status === 401) {
      err('Authentication error (401). Your API key may be invalid.');
      detail('Run: npx @ongarde/openclaw status');
      return { success: false, error: 'Auth error' };
    } else {
      // Not blocked — scanner may not be active
      err('Test credential was not blocked. Scanner may not be active.');
      detail('Check: npx @ongarde/openclaw status');
      return { success: false, error: 'Test block not blocked' };
    }
  } else {
    blank();
    ok('Protection already verified (previous run detected).');
    info(`Dashboard: http://localhost:${port}/dashboard`);
    blank();
  }

  return { success: true };
}

module.exports = { runInit };
