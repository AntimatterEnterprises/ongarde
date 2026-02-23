'use strict';
/**
 * OpenClaw config file detection and baseUrl update logic.
 * E-007-S-001: detect config, backup, update baseUrl.
 */

const fs = require('fs');
const os = require('os');
const path = require('path');

/**
 * Default OpenClaw config search paths in priority order.
 * Windows paths are added when running on Windows.
 */
function getDefaultConfigPaths() {
  const home = os.homedir();
  const paths = [
    path.join(home, '.openclaw', 'config.json'),
    path.join(home, '.config', 'openclaw', 'config.json'),
    path.join(home, '.config', 'openclaw', 'settings.json'),
  ];

  if (process.platform === 'win32') {
    const appData = process.env.APPDATA || '';
    const localAppData = process.env.LOCALAPPDATA || '';
    if (appData) paths.push(path.join(appData, 'openclaw', 'config.json'));
    if (localAppData) paths.push(path.join(localAppData, 'openclaw', 'config.json'));
  } else {
    paths.push('/etc/openclaw/config.json');
  }

  return paths;
}

/**
 * Auto-detect the OpenClaw config file.
 *
 * @param {string|null} [explicitPath] - Explicit path from --config flag
 * @returns {{ path: string, config: object }} Config path and parsed JSON
 * @throws {Error} With E2 error info if not found
 */
function findOpenClawConfig(explicitPath) {
  if (explicitPath) {
    if (!fs.existsSync(explicitPath)) {
      throw Object.assign(
        new Error(`Config not found at specified path: ${explicitPath}`),
        { code: 'CONFIG_NOT_FOUND', path: explicitPath, explicit: true }
      );
    }
    const config = JSON.parse(fs.readFileSync(explicitPath, 'utf8'));
    return { path: explicitPath, config };
  }

  const searchPaths = getDefaultConfigPaths();
  for (const p of searchPaths) {
    if (fs.existsSync(p)) {
      try {
        const config = JSON.parse(fs.readFileSync(p, 'utf8'));
        return { path: p, config };
      } catch {
        // Invalid JSON — skip and try next
      }
    }
  }

  throw Object.assign(
    new Error(`Config not found at any default path`),
    { code: 'CONFIG_NOT_FOUND', searchedPaths: searchPaths, explicit: false }
  );
}

/**
 * Extract the current baseUrl from an OpenClaw config object.
 * Supports:
 *   - config.models.providers[0].baseUrl
 *   - config.baseUrl (top-level)
 *
 * @param {object} config - Parsed OpenClaw config
 * @returns {{ baseUrl: string, location: 'provider'|'root', providerIndex: number }}
 * @throws {Error} If no baseUrl found
 */
function extractBaseUrl(config) {
  // Try models.providers[0].baseUrl first
  if (config.models && Array.isArray(config.models.providers) && config.models.providers.length > 0) {
    for (let i = 0; i < config.models.providers.length; i++) {
      const provider = config.models.providers[i];
      if (provider && typeof provider.baseUrl === 'string') {
        return { baseUrl: provider.baseUrl, location: 'provider', providerIndex: i };
      }
    }
  }

  // Try top-level baseUrl
  if (typeof config.baseUrl === 'string') {
    return { baseUrl: config.baseUrl, location: 'root', providerIndex: -1 };
  }

  throw Object.assign(
    new Error('No baseUrl field found in OpenClaw config'),
    { code: 'NO_BASE_URL' }
  );
}

/**
 * Update the baseUrl in an OpenClaw config object (in-place mutation).
 * Preserves all other fields.
 *
 * @param {object} config - Parsed OpenClaw config (mutated in place)
 * @param {string} newBaseUrl - New baseUrl value
 * @param {'provider'|'root'} location - Where to update
 * @param {number} providerIndex - Provider array index (if location='provider')
 * @returns {object} Updated config (same reference)
 */
function updateBaseUrl(config, newBaseUrl, location, providerIndex) {
  if (location === 'provider') {
    config.models.providers[providerIndex].baseUrl = newBaseUrl;
  } else {
    config.baseUrl = newBaseUrl;
  }
  return config;
}

/**
 * Write config back to disk, preserving formatting as best as possible.
 *
 * @param {string} configPath - Path to write
 * @param {object} config - Config object to serialize
 */
function writeOpenClawConfig(configPath, config) {
  const content = JSON.stringify(config, null, 2);
  fs.writeFileSync(configPath, content + '\n', 'utf8');
}

module.exports = {
  getDefaultConfigPaths,
  findOpenClawConfig,
  extractBaseUrl,
  updateBaseUrl,
  writeOpenClawConfig,
};
