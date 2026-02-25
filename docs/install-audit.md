# OnGarde Install Experience Audit

**Author:** Winston — Technical Architect  
**Date:** 2026-02-25  
**Scope:** Full install-experience audit covering installer code, integration layer, documentation, and first-timer journey.

---

## Executive Summary

The install experience has **three showstopper bugs** that mean `npx @ongarde/openclaw init` will fail for **100% of real OpenClaw users** in its current state. Beyond those blockers, there are pervasive inconsistencies between what our docs promise and what the code actually does. This must be fixed before any public launch.

---

## A. Critical Failure Points

### Step 0 — Prerequisites (never validated)

#### A-01: Python not installed (Likelihood: Medium)
- **What breaks:** `findOnGardePackageDir()` → `findPython()` throws
- **Error shown:** `Python 3 not found. Install Python 3.12+...`
- **Handled?** Partially — the error message mentions Python 3 but not the 3.12+ minimum version. The user sees this only after the wizard has already patched their OpenClaw config.
- **Unhandled edge:** Python 3.8 or 3.10 is installed. `findPython()` will succeed (it only checks for "Python 3" in stdout), but `uvicorn` and OnGarde itself will fail at runtime with cryptic import errors. **No version validation exists.**
- **Code reference (`process-utils.js` line 16-21):**
  ```javascript
  if (result.stdout && result.stdout.includes('Python 3')) return cmd;
  if (result.stderr && result.stderr.includes('Python 3')) return cmd;
  ```
  Accepts any Python 3.x — no minimum version enforcement.

#### A-02: OnGarde Python package not pip-installed (Likelihood: HIGH — default state for new users)
- **What breaks:** `findOnGardePackageDir()` runs `python -c "import importlib.util... find_spec('app.main')"`, returns empty string, throws.
- **Error shown:** `OnGarde Python package not found. Install it first:\n  pip install ongarde[full]`
- **When does this happen?** At Step 2 (start proxy) — AFTER the OpenClaw config has already been patched.
- **Critical:** The README's "Quick Start" shows one command: `npx @ongarde/openclaw init`. It says "Installs and starts OnGarde." **This is false.** The wizard never runs `pip install`. It assumes pip-installation already happened. A first-timer will hit this failure and have no idea what to do — their OpenClaw config is now broken.
- **Code reference (`init.js` line ~105-107):**
  ```javascript
  info('Installing OnGarde proxy...');
  // Package is already installed (this is the CLI running from the package)
  ok('Proxy installed (v1.0.0)');
  ```
  This comment admits the problem. "Package is already installed" is assumed, not verified.

#### A-03: uvicorn not installed (Likelihood: HIGH — companion to A-02)
- **What breaks:** `spawnProxy()` runs `python -m uvicorn app.main:app`. If uvicorn isn't in the Python environment, the process exits immediately with code 1.
- **Error shown:** `Proxy process exited unexpectedly (code: 1). Check logs: cat ~/.ongarde/proxy.log`
- **Handled?** Process crash detection works — but the user is directed to a log file they don't know how to interpret. The log will contain Python's `No module named uvicorn`, but there's no guidance.

#### A-04: spaCy `en_core_web_sm` model not downloaded (Likelihood: HIGH)
- **What breaks:** OnGarde starts, but the Presidio NLP scanner fails to load the language model. Scanner operates in degraded mode (or throws at startup).
- **Error shown:** Nothing in init wizard — it either starts "successfully" with a disabled scanner, or crashes silently.
- **QUICKSTART.md Step 2:** Lists `python -m spacy download en_core_web_sm` in the manual path but NOT in the one-command wizard path. The wizard never runs this.

---

### Step 1 — OpenClaw Config Detection

#### A-05: Wrong config filename (Likelihood: CERTAIN — affects 100% of users) 🔴 SHOWSTOPPER
- **What breaks:** `findOpenClawConfig()` searches for `config.json`. OpenClaw's actual config file is `openclaw.json`.
- **Searched paths (`openclaw-config.js` line 19-34):**
  ```javascript
  path.join(home, '.openclaw', 'config.json'),         // ← WRONG
  path.join(home, '.config', 'openclaw', 'config.json'), // ← WRONG
  path.join(home, '.config', 'openclaw', 'settings.json'), // ← WRONG
  ```
- **Actual OpenClaw config location:** `~/.openclaw/openclaw.json`
- **Confirmed by:** OpenClaw docs state "OpenClaw reads an optional JSON5 config from `~/.openclaw/openclaw.json`" and `OPENCLAW_CONFIG_PATH` env var points to this file.
- **Error shown:** `Config not found at default path (~/.openclaw/config.json)` — which itself references the wrong filename.
- **Handled?** Provides `--config` flag as escape hatch, but the error message directs users to a file that doesn't exist.

#### A-06: Config is JSON5, not regular JSON (Likelihood: HIGH)
- **What breaks:** `JSON.parse(fs.readFileSync(p, 'utf8'))` — OpenClaw's config is JSON5, which supports comments and trailing commas. `JSON.parse` will throw `SyntaxError: Unexpected token` on any config that uses these JSON5 features.
- **Code reference (`openclaw-config.js` line 51-52):**
  ```javascript
  const config = JSON.parse(fs.readFileSync(p, 'utf8'));
  ```
- **Error shown:** Uncaught `SyntaxError` (the `catch` block just skips to the next path and ultimately throws `CONFIG_NOT_FOUND`).

#### A-07: Wrong config format — providers is an object, not an array (Likelihood: CERTAIN) 🔴 SHOWSTOPPER
- **What breaks:** `extractBaseUrl()` checks `Array.isArray(config.models.providers)` — but OpenClaw's `models.providers` is an **object** keyed by provider name, not an array.
- **Code reference (`openclaw-config.js` line 74-85):**
  ```javascript
  if (config.models && Array.isArray(config.models.providers) && config.models.providers.length > 0) {
    for (let i = 0; i < config.models.providers.length; i++) {
      const provider = config.models.providers[i];
      if (provider && typeof provider.baseUrl === 'string') {
        return { baseUrl: provider.baseUrl, location: 'provider', providerIndex: i };
      }
    }
  }
  ```
- **Actual OpenClaw format (from `docs/providers/litellm.md`, `moonshot.md`, etc.):**
  ```json5
  {
    models: {
      providers: {
        litellm: {
          baseUrl: "http://localhost:4000",
          // ...
        }
      }
    }
  }
  ```
- `Array.isArray({})` is always `false`. The array path never executes.
- Falls through to `config.baseUrl` (top-level), which doesn't exist in OpenClaw config.
- **Error shown:** `Could not find baseUrl in config: No baseUrl field found in OpenClaw config`
- **Even if this were fixed:** `providerIndex` (array index) would need to become a `providerKey` (object key). The `updateBaseUrl()` function would also need to change.

#### A-08: No validation of config JSON against schema (Medium)
- The installer will happily attempt to patch an incomplete, empty, or unrelated JSON file if it happens to parse successfully and live at one of the searched paths.

---

### Step 1a — RAM Check / Lite Mode

#### A-09: Lite mode prompt logic is inverted (Likelihood: Medium — affects low-RAM users)
- **What breaks:** The prompt says "Enable Lite mode? [Y/n]" but the assignment inverts the result.
- **Code reference (`init.js` line ~142):**
  ```javascript
  useLite = !(await promptYesNo('  Enable Lite mode? [Y/n]: '));
  ```
  - `promptYesNo` returns `true` for Y/Enter.
  - `useLite = !true = false` → Full mode selected when user says "yes, enable lite".
  - `useLite = !false = true` → Lite mode selected when user says "no, don't enable lite".
- **Effect:** Users on low-RAM machines who type Y to enable Lite mode get Full mode. The prompt and behavior are opposite.

#### A-10: Lite mode YAML regex uses invalid syntax (Medium)
- **Code reference (`init.js` line ~157):**
  ```javascript
  const updated = ongardeConfig.includes('scanner:')
    ? ongardeConfig.replace(/scanner:[\s\S]*?(?=\n\S|\Z)/, 'scanner:\n  mode: lite\n')
    : ongardeConfig + '\nscanner:\n  mode: lite\n';
  ```
  - `\Z` is Python regex syntax for "end of string". **JavaScript does not support `\Z`.** In JavaScript, the regex will silently treat `\Z` as a literal character.
  - This can corrupt `config.yaml` on certain edge cases where `\Z` happens to interfere.

---

### Step 1b — Backup + BaseUrl Update

#### A-11: Config patched before Python/proxy is verified (High)
- The OpenClaw config is patched to point `baseUrl → http://localhost:4242/v1` in Step 1.
- Python validation and proxy startup happen in Step 2.
- If Step 2 fails, OpenClaw is broken (pointing to a dead proxy) with no auto-rollback.
- **Uninstall path exists?** Only if the user knows to run `npx @ongarde/openclaw uninstall` — which requires the wizard to have been documented in the failure output.

#### A-12: No rollback on Step 2/3 failure (High)
- The backup file is created but never used to auto-restore on failure.
- When the start command fails, init.js returns `{ success: false }` — it does NOT restore the OpenClaw config.

---

### Step 2 — Proxy Startup

#### A-13: Spawn command doesn't inherit pip environment (Medium)
- `spawnProxy()` runs `python -m uvicorn` with `env: { ...process.env }`.
- If the user installed OnGarde in a virtualenv that isn't activated, `process.env` won't have the venv's PATH. The Python found by `findPython()` may not be the same Python that has uvicorn installed.

#### A-14: 30-second timeout with no progress feedback (Medium)
- Spacy loads the NLP model at startup — this can take 20+ seconds on first run.
- During this time, the terminal shows only the initial spinner message. No progress indication.
- Users may assume it's hung and Ctrl+C.

#### A-15: Proxy log file not shown on success (Low)
- On success, log path is never revealed. On failure it is (good). But users troubleshooting slow startup have no way to find it.

---

### Step 3 — API Key Creation

#### A-16: API key creation sends wrong field name (Medium)
- **Code reference (`init.js` line ~77):**
  ```javascript
  body: JSON.stringify({ user_id: 'default' }),
  ```
- **QUICKSTART.md** documents the endpoint as: `{ "name": "my-agent" }` with `key` and `name` in the response.
- Inconsistency: `user_id` may not be a valid field name, causing a 422 validation error. In that case `keyResult` is `null` and the user sees "Could not create API key automatically."

#### A-17: API key shown once — no confirmation step (Low)
- The key is printed and immediately the wizard continues to the test block.
- No "Did you save this? Press Enter to continue" gate.
- A user who didn't notice or whose terminal scrolled will lose their key permanently.

---

### Step 4 — Test Block

#### A-18: Test block failure exits init as failure (Medium)
- If the scanner isn't fully initialized yet (still warming up), the test credential might not be blocked.
- **Code reference (`init.js`):**
  ```javascript
  } else {
    err('Test credential was not blocked. Scanner may not be active.');
    return { success: false, error: 'Test block not blocked' };
  }
  ```
- This can leave users in a partially-installed state thinking it failed, when in fact OnGarde is running fine — the scanner just needed a few more seconds.

---

## B. OpenClaw Integration Gaps

### B-01: Config filename mismatch 🔴 SHOWSTOPPER

| What we look for | What actually exists |
|---|---|
| `~/.openclaw/config.json` | `~/.openclaw/openclaw.json` |
| `~/.config/openclaw/config.json` | (doesn't exist) |
| `~/.config/openclaw/settings.json` | (doesn't exist) |

**Fix:** Change `getDefaultConfigPaths()` to look for `openclaw.json`:
```javascript
function getDefaultConfigPaths() {
  const home = os.homedir();
  return [
    path.join(home, '.openclaw', 'openclaw.json'),      // ← correct primary
    path.join(home, '.openclaw', 'config.json'),        // ← legacy fallback
    path.join(home, '.config', 'openclaw', 'openclaw.json'),
  ];
}
```

Also respect `OPENCLAW_CONFIG_PATH` env var (OpenClaw's documented override):
```javascript
if (process.env.OPENCLAW_CONFIG_PATH && fs.existsSync(process.env.OPENCLAW_CONFIG_PATH)) {
  return { path: process.env.OPENCLAW_CONFIG_PATH, config: parseJson5(process.env.OPENCLAW_CONFIG_PATH) };
}
```

### B-02: Config format mismatch — object vs. array 🔴 SHOWSTOPPER

OpenClaw `models.providers` is an object:
```json5
// ACTUAL OpenClaw config format
{
  models: {
    providers: {
      litellm: {
        baseUrl: "http://localhost:4000",
        apiKey: "${LITELLM_API_KEY}",
        api: "openai-completions",
        models: [...]
      }
    }
  }
}
```

Our code assumes an array:
```javascript
// WRONG — will never execute
if (config.models && Array.isArray(config.models.providers) && ...) {
  for (let i = 0; i < config.models.providers.length; i++) {
```

**Fix:** Rewrite `extractBaseUrl()` to iterate object keys:
```javascript
function extractBaseUrl(config) {
  // Try models.providers.{name}.baseUrl (object keyed by provider name)
  if (config.models && config.models.providers && typeof config.models.providers === 'object' && !Array.isArray(config.models.providers)) {
    for (const [providerKey, provider] of Object.entries(config.models.providers)) {
      if (provider && typeof provider.baseUrl === 'string') {
        return { baseUrl: provider.baseUrl, location: 'provider', providerKey };
      }
    }
  }
  // Fallback: top-level baseUrl
  if (typeof config.baseUrl === 'string') {
    return { baseUrl: config.baseUrl, location: 'root', providerKey: null };
  }
  throw Object.assign(
    new Error('No baseUrl field found in OpenClaw config'),
    { code: 'NO_BASE_URL' }
  );
}
```

Also update `updateBaseUrl()` to use `providerKey` (string) instead of `providerIndex` (integer):
```javascript
function updateBaseUrl(config, newBaseUrl, location, providerKey) {
  if (location === 'provider') {
    config.models.providers[providerKey].baseUrl = newBaseUrl;
  } else {
    config.baseUrl = newBaseUrl;
  }
  return config;
}
```

### B-03: JSON5 parsing required

OpenClaw's config uses JSON5 (comments and trailing commas are valid). `JSON.parse` will fail on any real-world config.

**Fix:** Add a JSON5 parser. The `json5` npm package is lightweight:
```javascript
const JSON5 = require('json5');
// ...
const config = JSON5.parse(fs.readFileSync(p, 'utf8'));
```

When writing back, use `JSON.stringify` (not JSON5) to stay compatible — OpenClaw accepts standard JSON too.

### B-04: `OPENCLAW_CONFIG_PATH` env var not respected

OpenClaw itself respects `OPENCLAW_CONFIG_PATH` as the config file override. Our installer doesn't check this variable. A user with a non-standard install location who sets this env var will still get `CONFIG_NOT_FOUND`.

**Fix:** Check `OPENCLAW_CONFIG_PATH` first, before the default path search.

### B-05: Proxy config architecture mismatch

OnGarde's approach sets ONE provider's `baseUrl` to `http://localhost:4242/v1`. But OpenClaw routes different providers to different `baseUrl`s — `openai` provider goes to OpenAI, `anthropic` goes to Anthropic, etc.

**Implication:** If a user has multiple configured providers, only the first one found gets patched. Their requests to other providers bypass OnGarde entirely, with no warning.

**Architectural recommendation:** For complete coverage, init.js should:
1. Enumerate all providers in `models.providers`
2. Prompt "We'll route these providers through OnGarde: [list]. Confirm?" 
3. Patch all of them — or explain clearly which ones are covered

### B-06: QUICKSTART.md shows wrong config format

```yaml
# WRONG — from QUICKSTART.md
# OpenClaw config.yaml       ← Wrong extension (it's .json, not .yaml)
models:
  providers:
    baseUrl: http://127.0.0.1:4242/v1   ← Wrong structure (baseUrl not a direct child of providers)
```

Correct format:
```json5
// ~/.openclaw/openclaw.json
{
  models: {
    providers: {
      ongarde: {
        baseUrl: "http://127.0.0.1:4242/v1",
        apiKey: "${ONGARDE_API_KEY}",
        api: "openai-completions",
        models: [{ id: "passthrough", name: "OnGarde Proxy" }]
      }
    }
  },
  agents: {
    defaults: { model: { primary: "ongarde/passthrough" } }
  }
}
```

---

## C. The "Skeptical First-Timer" Journey

**Persona:** Alex. Has OpenClaw running. Knows Python vaguely. Will read the README once, run the command, and give up if something goes wrong without a clear fix.

### Journey Map

```
READ README.md (about 3 minutes)
  ↓
"npx @ongarde/openclaw init" ← Alex types this
  ↓
  ✓ Brand header shown
  ✓ "Installing OnGarde proxy..."
  ✓ "Proxy installed (v1.0.0)"  ← Fake! Nothing was installed!
  ↓
  ✗ FAIL: "Config not found at default path (~/.openclaw/config.json)"
  
  ← DROP-OFF POINT 1 (HIGH likelihood)
    Alex KNOWS their config is at ~/.openclaw/openclaw.json 
    but the error says "config.json". Alex may:
    a) Copy openclaw.json to config.json (wrong, won't work)
    b) Try --config ~/.openclaw/openclaw.json (correct guess)
    c) Give up
```

**If Alex guesses (b) and retries with `--config ~/.openclaw/openclaw.json`:**

```
  ↓
  ✓ "Config found: ~/.openclaw/openclaw.json"
  ↓
  ✗ FAIL (likely): SyntaxError parsing JSON5 config
    OR
  ✗ FAIL: "Could not find baseUrl in config: No baseUrl field found"
  
  ← DROP-OFF POINT 2 (CERTAIN — every user with models.providers configured)
    No actionable fix provided.
    Alex gives up.
```

**If Alex has the rare case of a minimal config with no `models.providers` and no JSON5 syntax:**

```
  ↓
  ✓ Config parsed (minimal JSON works)
  ✗ FAIL: "Could not find baseUrl in config: No baseUrl field found"
  
  ← DROP-OFF POINT 3
    Error message says "Your config may have an unsupported format."
    This is unhelpful. Alex doesn't know what format to use.
```

**The one path to success:** Alex somehow has both `config.json` AND a `models.providers[0].baseUrl` as an array entry (impossible in real OpenClaw) AND standard JSON (no comments/trailing commas). Probability: ~0%.

**After all config issues are theoretically fixed, the next drop-off:**

```
  ↓
  "Starting proxy on port 4242..."
  ↓
  ✗ FAIL: "OnGarde Python package not found. Install it first: pip install ongarde[full]"
  
  ← DROP-OFF POINT 4 (100% of users who only ran "npx @ongarde/openclaw init")
    README claimed the wizard "installs and starts OnGarde."
    Alex expected this to be automatic. It isn't.
    Alex has to manually pip install and restart.
```

**Assuming Alex pip-installs everything correctly:**

```
  ↓
  [Wait 20-30 seconds while spaCy loads, no feedback]
  
  ← DROP-OFF POINT 5 (Medium likelihood)
    Alex assumes it's hung and hits Ctrl+C.
```

**If they wait:**

```
  ✓ Proxy started
  ✓ Health check passed
  ✓ API key printed: ong-xxxxx
  
  [Terminal immediately continues to "Test Block" section]
  
  ← DROP-OFF POINT 6 (Medium likelihood)
    Key scrolls off screen. Alex didn't copy it. Lost forever.
```

---

## D. Intelligent Installer Recommendations

### D-01: Pre-flight Checks (add at the top of `runInit()`)

```javascript
async function runPreflightChecks({ port }) {
  const issues = [];

  // 1. Check Node.js version (we need native fetch = Node 18+)
  const nodeVer = parseInt(process.version.replace('v', '').split('.')[0], 10);
  if (nodeVer < 18) {
    issues.push({
      level: 'fatal',
      message: `Node.js 18+ required (you have ${process.version}). Update at: https://nodejs.org`
    });
  }

  // 2. Check Python version (3.12+)
  try {
    const python = findPython();
    const result = spawnSync(python, ['-c', 'import sys; print(sys.version_info[:2])'], { encoding: 'utf8' });
    const match = (result.stdout || '').match(/\((\d+), (\d+)\)/);
    if (match) {
      const [major, minor] = [parseInt(match[1]), parseInt(match[2])];
      if (major < 3 || (major === 3 && minor < 12)) {
        issues.push({
          level: 'fatal',
          message: `Python 3.12+ required (you have ${major}.${minor}). Update at: https://python.org/downloads`
        });
      }
    }
  } catch {
    issues.push({ level: 'fatal', message: 'Python 3 not found. Install Python 3.12+: https://python.org/downloads' });
  }

  // 3. Check pip available
  try {
    const python = findPython();
    const result = spawnSync(python, ['-m', 'pip', '--version'], { encoding: 'utf8' });
    if (result.status !== 0) throw new Error();
  } catch {
    issues.push({ level: 'fatal', message: 'pip not found. Install pip: https://pip.pypa.io/en/stable/installation/' });
  }

  // 4. Check port availability
  const portInUse = await isPortInUse(port);
  if (portInUse) {
    // Check if it's OnGarde already running
    const health = await checkHealth(port, 1000);
    if (health.ok) {
      issues.push({ level: 'warn', message: `OnGarde is already running on port ${port}. Re-running init will reconfigure it.` });
    } else {
      issues.push({
        level: 'fatal',
        message: `Port ${port} is already in use by another process.\n  Fix: npx @ongarde/openclaw init --port 8042\n  Or find and kill: lsof -ti:${port} | xargs kill -9`
      });
    }
  }

  // 5. Check OnGarde Python package + key dependencies
  try {
    findOnGardePackageDir();
  } catch {
    issues.push({
      level: 'fatal',
      message: 'OnGarde Python package not installed.',
      fix: async () => {
        info('Installing OnGarde Python package...');
        const python = findPython();
        const result = spawnSync(python, ['-m', 'pip', 'install', 'ongarde[full]'], {
          encoding: 'utf8', stdio: 'inherit'
        });
        if (result.status !== 0) throw new Error('pip install failed');
        ok('OnGarde Python package installed.');
        // Also download spacy model
        info('Downloading language model...');
        const spacy = spawnSync(python, ['-m', 'spacy', 'download', 'en_core_web_sm'], {
          encoding: 'utf8', stdio: 'inherit'
        });
        if (spacy.status !== 0) warn('spaCy model download failed — scanner will use lite mode.');
      }
    });
  }

  return issues;
}
```

### D-02: Auto-detection of OpenClaw Config

Replace the current path search with config that also respects OpenClaw env vars:

```javascript
function getDefaultConfigPaths() {
  const paths = [];

  // 1. Respect OpenClaw's own env var (highest priority)
  if (process.env.OPENCLAW_CONFIG_PATH) {
    paths.push(process.env.OPENCLAW_CONFIG_PATH);
  }

  const home = os.homedir();

  // 2. Standard OpenClaw locations (correct filenames)
  paths.push(path.join(home, '.openclaw', 'openclaw.json'));
  paths.push(path.join(home, '.openclaw', 'config.json')); // legacy

  // 3. XDG base dir
  const xdgConfig = process.env.XDG_CONFIG_HOME || path.join(home, '.config');
  paths.push(path.join(xdgConfig, 'openclaw', 'openclaw.json'));

  // 4. Windows AppData
  if (process.platform === 'win32') {
    const appData = process.env.APPDATA || '';
    if (appData) paths.push(path.join(appData, 'openclaw', 'openclaw.json'));
  }

  // 5. System-wide
  if (process.platform !== 'win32') {
    paths.push('/etc/openclaw/openclaw.json');
  }

  return paths;
}
```

When config is not found, show users ALL the paths searched AND the right env var:
```
  ✗ OpenClaw config not found. Searched:
      ~/.openclaw/openclaw.json
      ~/.config/openclaw/openclaw.json

  To specify your config manually:
    npx @ongarde/openclaw init --config /path/to/openclaw.json

  Or set OPENCLAW_CONFIG_PATH=/path/to/openclaw.json and re-run.

  If OpenClaw is freshly installed, run:
    openclaw configure
  ...then re-run this wizard.
```

### D-03: Clear, Actionable Error Messages

Replace each existing vague error with specific guidance:

| Current Error | Proposed Replacement |
|---|---|
| `"Config not found at default path (~/.openclaw/config.json)"` | `"OpenClaw config not found at ~/.openclaw/openclaw.json\n  Run: openclaw configure\n  Or: npx @ongarde/openclaw init --config /path/to/openclaw.json"` |
| `"Could not find baseUrl in config: No baseUrl field found"` | `"No LLM provider configured in your OpenClaw config.\n  Run 'openclaw models set <provider/model>' to configure a provider first.\n  Then re-run: npx @ongarde/openclaw init"` |
| `"OnGarde Python package not found."` | `"OnGarde's Python component isn't installed yet.\n  Fix: pip install ongarde[full]\n       python -m spacy download en_core_web_sm\n  Then re-run: npx @ongarde/openclaw init"` |
| `"Proxy process exited unexpectedly (code: 1)."` | `"OnGarde failed to start. Most likely cause: missing Python dependency.\n  Try: pip install ongarde[full]\n  Full error log: cat ~/.ongarde/proxy.log\n  Look for 'ModuleNotFoundError' or 'ImportError'"` |

### D-04: Auto-Rollback on Failure

Add a rollback function called on any failure after config patch:

```javascript
async function rollbackConfigIfNeeded(openClawConfigPath, openClawConfig, originalBaseUrl, location, providerKey) {
  warn('Restoring your OpenClaw config to its original state...');
  updateBaseUrl(openClawConfig, originalBaseUrl, location, providerKey);
  try {
    writeOpenClawConfig(openClawConfigPath, openClawConfig);
    ok('Config restored. Your OpenClaw setup is unchanged.');
  } catch (e) {
    err('IMPORTANT: Could not restore config automatically!');
    err(`Manually restore baseUrl to: ${originalBaseUrl}`);
    err(`In file: ${openClawConfigPath}`);
  }
}
```

Call this in the failure path of init.js Steps 2 and 3.

### D-05: Install Python Dependencies Automatically

Replace the fake "installing" step with real work:

```javascript
// Step 0.5: Install Python package if needed
if (!isOnGardeInstalled()) {
  info('Installing OnGarde Python package...');
  spin('Running pip install ongarde[full] (this may take 1-2 minutes)...');
  const installResult = await runPipInstall();
  if (!installResult.success) {
    err(`pip install failed: ${installResult.error}`);
    err('Make sure pip is available: python3 -m pip --version');
    return { success: false, error: 'pip install failed' };
  }
  ok('Python package installed.');

  info('Downloading NLP model (one-time, ~40 MB)...');
  spin('Downloading en_core_web_sm...');
  const spacyResult = await runSpacyDownload();
  if (!spacyResult.success) {
    warn('NLP model download failed. Scanner will use Lite mode.');
  } else {
    ok('NLP model ready.');
  }
}
```

### D-06: End-to-End Verification

After the proxy starts, do a real verification pass:

```javascript
async function verifyEndToEnd({ port, apiKey }) {
  const checks = [];

  // 1. Health check
  const health = await checkHealth(port, 3000);
  checks.push({ name: 'Proxy running', pass: health.ok });

  // 2. Scanner status
  const scannerOk = health.body?.scanner === 'healthy';
  checks.push({ name: 'Scanner active', pass: scannerOk, 
    warn: !scannerOk ? 'Scanner warming up — check again in 30 seconds' : null });

  // 3. Auth working
  if (apiKey) {
    const authCheck = await checkAuth(port, apiKey);
    checks.push({ name: 'API auth', pass: authCheck.ok,
      fail: !authCheck.ok ? 'API key rejected — run: npx @ongarde/openclaw status' : null });
  }

  // 4. OpenClaw can reach OnGarde (check if OC config was patched successfully)
  checks.push({ name: 'OpenClaw config patched', pass: true }); // we know this if we got here

  return checks;
}
```

### D-07: Fix the Lite Mode Prompt Inversion

```javascript
// CURRENT (broken):
useLite = !(await promptYesNo('  Enable Lite mode? [Y/n]: '));

// FIXED:
useLite = await promptYesNo('  Enable Lite mode? [Y/n]: ');
```

### D-08: Key Save Confirmation

After printing the API key, gate on user input:
```javascript
ok('API key created (save this now — shown once):');
blank();
process.stdout.write(`      ${apiKeyPlaintext}\n`);
blank();
if (process.stdout.isTTY && !yes) {
  await promptContinue('  [Confirm you have saved this key — press Enter to continue] ');
}
```

### D-09: Progress Feedback During Startup

```javascript
// In runStart(), add a progress ticker during the 30s polling window:
let dots = 0;
const ticker = setInterval(() => {
  dots++;
  if (dots % 4 === 0) {
    process.stdout.write('\r  Loading NLP scanner... (this takes ~20s on first run)   ');
  }
}, 500);
// Clear ticker when done:
clearInterval(ticker);
process.stdout.write('\r                                                             \r');
```

---

## E. Documentation Rewrite Recommendations

### E-01: README.md — "Quick Start" Section Is Misleading

**Problem:** The one-command install promise is currently false.

```markdown
### OpenClaw (One Command)
npx @ongarde/openclaw init
```
"This wizard: 1. Installs and starts OnGarde..."

**Fix:** Either fix the code to make this true (recommended), or add a prerequisite step:

```markdown
### Option A — OpenClaw (Recommended)

**Prerequisites:** Python 3.12+ and pip installed.

```bash
npx @ongarde/openclaw init
```

This wizard:
1. Installs the OnGarde Python package and NLP model (one-time ~2 min)
2. Detects your OpenClaw config and configures OnGarde as your security proxy
3. Starts OnGarde and verifies protection is active
4. Creates your first API key
```

### E-02: QUICKSTART.md — Wrong Config Format

Remove or rewrite the "OpenClaw config.yaml" code block. Replace with:

```markdown
OnGarde patches your OpenClaw config automatically via `npx @ongarde/openclaw init`.
If you need to configure it manually, OnGarde sets up a custom provider in your
`~/.openclaw/openclaw.json`:

```json5
// ~/.openclaw/openclaw.json (added by OnGarde init wizard)
{
  models: {
    providers: {
      ongarde: {
        baseUrl: "http://127.0.0.1:4242/v1",
        apiKey: "${ONGARDE_API_KEY}",
        api: "openai-completions",
        models: [{ id: "passthrough", name: "via OnGarde" }]
      }
    }
  }
}
```

### E-03: QUICKSTART.md — Prerequisites Section Incomplete

**Current:**
```markdown
- Python 3.12+ (for manual setup)
- Node.js 18+ (for the OpenClaw one-command installer)
```

**Fix:** Remove the "(for manual setup)" qualifier — Python is ALSO required for the one-command installer:
```markdown
- **Python 3.12+** — required for all install methods (OnGarde's security engine is Python)
- **pip** — `python3 -m pip --version` should return a version
- **Node.js 18+** — required for the one-command OpenClaw installer (`npx`)
- **spaCy en_core_web_sm** — downloaded automatically by the wizard
```

### E-04: .env.example Has Wrong Variable Names

Current `.env.example`:
```
API_HOST=0.0.0.0
API_PORT=8000
SUPABASE_URL=your_supabase_url  # ← This is a dev remnant
```

Correct variables (from `.ongarde/config.yaml.example` and QUICKSTART.md):
```bash
# OnGarde Configuration
ONGARDE_PORT=4242          # Override default port
ONGARDE_AUTH_REQUIRED=true # Set to 'false' for local dev only
ONGARDE_CONFIG=            # Optional: explicit config path
DEBUG=false                # Set to 'true' for dev mode (enables /docs)
```

### E-05: README.md — Manual Setup Missing spaCy Step

The manual setup section:
```bash
# 2. Install Python dependencies
pip install -r requirements.txt
python -m spacy download en_core_web_sm   # ← MISSING from README
```

### E-06: QUICKSTART.md — "Point Your Agent at OnGarde" Section Uses Wrong Header Format

```yaml
# OpenClaw config.yaml     ← Wrong file, wrong format
models:
  providers:
    baseUrl: http://127.0.0.1:4242/v1   ← Wrong structure
```

This entire block needs to be replaced with correct OpenClaw JSON5 format (see E-02).

### E-07: Missing Troubleshooting Section

Neither README nor QUICKSTART has a troubleshooting section. Minimum needed:

| Symptom | Fix |
|---|---|
| `Config not found` | `npx @ongarde/openclaw init --config ~/.openclaw/openclaw.json` |
| `Python 3 not found` | Install Python 3.12+ from python.org |
| `OnGarde Python package not found` | `pip install ongarde[full]` |
| Port 4242 in use | `npx @ongarde/openclaw init --port 8042` |
| Health check: no response | `cat ~/.ongarde/proxy.log` |
| Scanner not healthy | `python -m spacy download en_core_web_sm` and restart |
| OpenClaw not using OnGarde | Verify with `openclaw config get models.providers` |

### E-08: README Architecture Diagram Description Is Inaccurate

> "Zero code changes required — just point your `baseUrl` at OnGarde."

OpenClaw doesn't have a single global `baseUrl`. This framing is correct for direct SDK use but misleading for the OpenClaw integration scenario. Add:

> "For OpenClaw: the init wizard automatically configures OnGarde as a custom provider in your `openclaw.json` — no manual editing needed."

### E-09: packages/openclaw/README.md Confusion

The package README leads with implementation notes ("This npm package is implemented in E-007") and story references. End users shouldn't see this.

The public README for `@ongarde/openclaw` should focus on:
1. What it does (in one sentence)
2. Install command
3. Commands and their purpose
4. Troubleshooting

Story references and AC numbers belong in `CHANGELOG.md`, not the published README.

---

## F. Priority Matrix

| ID | Finding | Severity | Effort | Impact |
|----|---------|----------|--------|--------|
| A-05 / B-01 | Wrong config filename (`config.json` → `openclaw.json`) | 🔴 CRITICAL | XS (30min) | Blocks 100% of users |
| A-07 / B-02 | `models.providers` is an object, not an array | 🔴 CRITICAL | S (2h) | Blocks 100% of users with providers configured |
| A-02 / D-05 | Wizard never pip-installs Python package | 🔴 CRITICAL | M (4h) | Blocks 100% of new users |
| B-03 | Config is JSON5 — `JSON.parse` fails | 🔴 CRITICAL | S (1h) | Blocks all real configs |
| E-02 / E-06 | QUICKSTART shows wrong config format | 🔴 CRITICAL | XS (30min) | Misleads all manual users |
| A-09 | Lite mode prompt logic inverted | 🔴 HIGH | XS (5min) | Wrong mode selected for RAM-limited users |
| A-10 | Lite mode YAML regex uses invalid `\Z` syntax | 🔴 HIGH | XS (10min) | Config corruption on lite-RAM path |
| A-11/A-12 | Config patched before proxy verified; no rollback | 🔴 HIGH | M (3h) | Leaves OpenClaw broken on failed install |
| B-04 | `OPENCLAW_CONFIG_PATH` env var not respected | 🟠 HIGH | XS (30min) | Non-standard installs silently fail |
| A-01 | Python version not validated (accepts 3.8, 3.10) | 🟠 HIGH | XS (30min) | Silent runtime failures |
| A-04 | spaCy model never downloaded in wizard | 🟠 HIGH | M (2h) | Scanner degraded/broken silently |
| D-01 | No pre-flight checks | 🟠 HIGH | M (4h) | All failure modes hit late, after config modified |
| A-16 | API key creation sends wrong field (`user_id` vs `name`) | 🟠 HIGH | XS (15min) | Key creation silently fails |
| B-05 | Only first provider patched; others bypass OnGarde | 🟠 HIGH | L (8h) | Security coverage gaps |
| A-17 | API key shown without save confirmation | 🟡 MEDIUM | XS (15min) | Users lose their only key |
| A-14 | 30s wait with no progress feedback | 🟡 MEDIUM | S (1h) | Users Ctrl+C thinking it's hung |
| A-13 | virtualenv path isolation (uvicorn not found) | 🟡 MEDIUM | S (2h) | Fails for venv users |
| A-18 | Test block failure treated as fatal (scanner warmup) | 🟡 MEDIUM | S (1h) | False failure on slow hardware |
| E-04 | `.env.example` has wrong variable names | 🟡 MEDIUM | XS (20min) | Manual setup confusion |
| E-03 | Prerequisites missing Python for wizard path | 🟡 MEDIUM | XS (20min) | Users don't know they need Python |
| A-03 | uvicorn not found → cryptic crash log | 🟡 MEDIUM | S (1h) | Bad DX, no guidance |
| E-07 | No troubleshooting section in docs | 🟡 MEDIUM | S (2h) | Support burden, user abandonment |
| A-15 | Log path not shown on success | 🟢 LOW | XS (5min) | Mild |
| E-09 | Package README shows internal story refs | 🟢 LOW | XS (20min) | Unprofessional |
| init.js duplicate `'use strict'` | Code hygiene | 🟢 LOW | XS (1min) | Cosmetic |

---

## Immediate Action Plan (Fix Order)

**Sprint goal: Make `npx @ongarde/openclaw init` work for a real OpenClaw user.**

### Must-fix before any user-facing release

1. **B-01** — Fix config filename to `openclaw.json` + respect `OPENCLAW_CONFIG_PATH`
2. **B-02** — Fix `models.providers` from array to object iteration
3. **B-03** — Add JSON5 parsing (`npm install json5`)
4. **A-02 + D-05** — Add actual `pip install ongarde[full]` + spaCy download step in wizard
5. **E-02 + E-06** — Fix QUICKSTART.md config format (5-minute fix)
6. **A-09** — Fix Lite mode prompt inversion (one-line fix)
7. **A-10** — Fix `\Z` → use `$` in JavaScript regex

### High priority (second pass)

8. **A-12** — Add config rollback on failure
9. **A-16** — Fix API key creation body field
10. **D-01** — Add pre-flight checks (Python version, pip, port, existing OnGarde)
11. **B-04** — Respect `OPENCLAW_CONFIG_PATH`
12. **A-17** — Add key save confirmation gate
13. **D-09** — Add progress feedback during 30s startup wait

---

*Audit complete. The root cause of most issues is that the installer was coded against an assumed OpenClaw config format that doesn't match reality. The two-line fix for B-01 (wrong filename) and the rewrite of `extractBaseUrl()` for B-02 (wrong structure) would unblock the majority of first-time failures. Everything else is refinement.*
