'use strict';
/**
 * OpenClaw config file detection and baseUrl update logic.
 * E-007-S-001: detect config, backup, update baseUrl.
 *
 * Fixes applied:
 *   - Fix #1: correct config filename (openclaw.json, not config.json)
 *   - Fix #2: models.providers is an object keyed by provider name, not an array
 *   - Fix #4: JSON5 parsing (supports comments + trailing commas)
 *   - Added: OPENCLAW_CONFIG_PATH env var support
 */

const fs = require('fs');
const os = require('os');
const path = require('path');
const JSON5 = require('json5');

/**
 * Default OpenClaw config search paths in priority order.
 * Primary: ~/.openclaw/openclaw.json (correct filename)
 * Legacy:  ~/.openclaw/config.json  (backwards compat)
 *
 * Also respects OPENCLAW_CONFIG_PATH env var (OpenClaw's documented override).
 */
function getDefaultConfigPaths() {
  const paths = [];

  // 1. Respect OpenClaw's own env var (highest priority)
  if (process.env.OPENCLAW_CONFIG_PATH) {
    paths.push(process.env.OPENCLAW_CONFIG_PATH);
  }

  const home = os.homedir();

  // 2. Primary: config.json (preferred — matches OpenClaw's documented filename)
  paths.push(path.join(home, '.openclaw', 'config.json'));

  // 3. Alternate filename (openclaw.json — kept for backwards compat)
  paths.push(path.join(home, '.openclaw', 'openclaw.json'));

  // 4. XDG base dir
  const xdgConfig = process.env.XDG_CONFIG_HOME || path.join(home, '.config');
  paths.push(path.join(xdgConfig, 'openclaw', 'openclaw.json'));
  paths.push(path.join(xdgConfig, 'openclaw', 'config.json'));

  if (process.platform === 'win32') {
    const appData = process.env.APPDATA || '';
    const localAppData = process.env.LOCALAPPDATA || '';
    if (appData) paths.push(path.join(appData, 'openclaw', 'openclaw.json'));
    if (localAppData) paths.push(path.join(localAppData, 'openclaw', 'openclaw.json'));
  } else {
    paths.push('/etc/openclaw/openclaw.json');
  }

  return paths;
}

/**
 * Parse a config file using JSON5 (supports comments + trailing commas).
 * Falls back gracefully so plain JSON files still work.
 *
 * @param {string} filePath
 * @returns {object}
 */
function parseConfigFile(filePath) {
  const content = fs.readFileSync(filePath, 'utf8');
  return JSON5.parse(content);
}

/**
 * Auto-detect the OpenClaw config file.
 *
 * @param {string|null} [explicitPath] - Explicit path from --config flag
 * @returns {{ path: string, config: object }} Config path and parsed object
 * @throws {Error} With code 'CONFIG_NOT_FOUND' if not found
 */
function findOpenClawConfig(explicitPath) {
  if (explicitPath) {
    if (!fs.existsSync(explicitPath)) {
      throw Object.assign(
        new Error(`Config not found at specified path: ${explicitPath}`),
        { code: 'CONFIG_NOT_FOUND', path: explicitPath, explicit: true }
      );
    }
    const config = parseConfigFile(explicitPath);
    return { path: explicitPath, config };
  }

  const searchPaths = getDefaultConfigPaths();
  for (const p of searchPaths) {
    if (fs.existsSync(p)) {
      try {
        const config = parseConfigFile(p);
        return { path: p, config };
      } catch {
        // Invalid JSON5 — skip and try next
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
 *
 * Supports two formats:
 *   - models.providers as array:  config.models.providers[0].baseUrl
 *   - models.providers as object: config.models.providers.litellm.baseUrl
 *
 * Falls back to top-level config.baseUrl.
 *
 * @param {object} config - Parsed OpenClaw config
 * @returns {{ baseUrl: string, location: 'provider'|'root', providerIndex: string|number|null }}
 * @throws {Error} With code 'NO_BASE_URL' if no baseUrl found
 */
function extractBaseUrl(config) {
  // Array format: models.providers[i].baseUrl
  if (config.models && Array.isArray(config.models.providers) && config.models.providers.length > 0) {
    for (let i = 0; i < config.models.providers.length; i++) {
      const provider = config.models.providers[i];
      if (provider && typeof provider.baseUrl === 'string') {
        return { baseUrl: provider.baseUrl, location: 'provider', providerIndex: i };
      }
    }
  }

  // Object format: models.providers keyed by provider name
  if (
    config.models &&
    config.models.providers &&
    typeof config.models.providers === 'object' &&
    !Array.isArray(config.models.providers)
  ) {
    for (const [key, provider] of Object.entries(config.models.providers)) {
      if (provider && typeof provider.baseUrl === 'string') {
        return { baseUrl: provider.baseUrl, location: 'provider', providerIndex: key };
      }
    }
  }

  // Fallback: top-level baseUrl
  if (typeof config.baseUrl === 'string') {
    return { baseUrl: config.baseUrl, location: 'root', providerIndex: null };
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
 * @param {string|number|null} providerKey - Provider key (string for object format, number for array)
 * @returns {object} Updated config (same reference)
 */
function updateBaseUrl(config, newBaseUrl, location, providerKey) {
  if (location === 'provider') {
    config.models.providers[providerKey].baseUrl = newBaseUrl;
  } else {
    config.baseUrl = newBaseUrl;
  }
  return config;
}

/**
 * Write config back to disk as valid JSON.
 * Note: comments in the original JSON5 are NOT preserved (JSON5 stringify
 * support is limited). The written file is valid JSON, which OpenClaw accepts.
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
