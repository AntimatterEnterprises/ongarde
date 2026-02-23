'use strict';
/**
 * Terminal display utilities for OnGarde CLI.
 * Handles formatting, colors, and structured output.
 */

const BRAND_HEADER = `
╔══════════════════════════════════════════════════════╗
║          OnGarde — Runtime AI Security               ║
║          https://ongarde.io                          ║
╚══════════════════════════════════════════════════════╝
`.trim();

/**
 * Print the brand header box (only on fresh install).
 */
function printBrandHeader() {
  console.log('\n' + BRAND_HEADER + '\n');
}

/**
 * Print a success line: "  ✓ <message>"
 */
function ok(msg) {
  process.stdout.write(`  ✓ ${msg}\n`);
}

/**
 * Print an error line: "  ✗ <message>"
 */
function err(msg) {
  process.stdout.write(`  ✗ ${msg}\n`);
}

/**
 * Print an info/progress line: "  ⟳ <message>"
 */
function spin(msg) {
  process.stdout.write(`  ⟳ ${msg}\n`);
}

/**
 * Print an indented detail line: "    → <detail>"
 */
function detail(msg) {
  process.stdout.write(`    → ${msg}\n`);
}

/**
 * Print a warning line: "  ⚠  <message>"
 */
function warn(msg) {
  process.stdout.write(`  ⚠  ${msg}\n`);
}

/**
 * Print a plain line with leading spaces.
 */
function info(msg) {
  process.stdout.write(`  ${msg}\n`);
}

/**
 * Print a blank line.
 */
function blank() {
  process.stdout.write('\n');
}

/**
 * Print a section separator.
 */
function separator(label) {
  if (label) {
    const line = `── ${label} `;
    const pad = '─'.repeat(Math.max(0, 57 - line.length));
    process.stdout.write(`  ${line}${pad}\n`);
  } else {
    process.stdout.write(`  ${'─'.repeat(57)}\n`);
  }
}

/**
 * Render the aha moment block box.
 */
function renderAhaMoment({ ruleId, riskLevel, scanId }) {
  const W = 57; // inner width
  const pad = (s, w) => (s || '').padEnd(w);

  const lines = [
    `│  🎉  OnGarde blocked a threat.                          │`,
    `│                                                         │`,
    `│  Rule:          ${pad(ruleId, 36)}│`,
    `│  Risk level:    ${pad(riskLevel, 36)}│`,
    `│  Content type:  ${pad('API key pattern (test credential)', 36)}│`,
    `│  Action:        ${pad('BLOCKED ✓', 36)}│`,
    `│  Test event:    ${pad('yes — quota unaffected', 36)}│`,
    `│                                                         │`,
    `│  scan_id: ${pad(scanId || 'unknown', 43)}│`,
  ];

  console.log(`  ┌${'─'.repeat(W)}┐`);
  lines.forEach(l => console.log(`  ${l}`));
  console.log(`  └${'─'.repeat(W)}┘`);
}

/**
 * Render the status output.
 */
function renderStatus(status) {
  const W = 57;
  console.log(`${'─'.repeat(W + 4)}`);
  console.log('OnGarde Status');
  console.log(`${'─'.repeat(W + 4)}`);

  if (status.proxyRunning) {
    console.log(`Proxy:          ✓ Running  (PID ${status.pid || '?'}, port ${status.port || 4242})`);
    if (status.health) {
      const mode = status.health.scanner_mode === 'lite' ? '[Lite mode]' : '[Full mode]';
      console.log(`Scanner:        ✓ ${status.health.scanner}  ${mode}`);
      if (status.health.avg_scan_ms !== undefined) {
        console.log(`  Avg latency:  ${status.health.avg_scan_ms.toFixed(1)}ms`);
      }
      if (status.health.queue_depth !== undefined) {
        console.log(`  Queue depth:  ${status.health.queue_depth}`);
      }
      if (status.entitySet) {
        console.log(`  Entity set:   ${status.entitySet.join(', ')}`);
      }
    }
  } else if (status.stalePid) {
    console.log(`Proxy:          ✗ Stopped (stale PID file — run: rm ~/.ongarde/proxy.pid)`);
    console.log(`Scanner:        offline`);
  } else {
    console.log(`Proxy:          ✗ Stopped`);
    console.log(`Scanner:        offline`);
  }

  if (status.apiKeyMasked) {
    const lastUsed = status.apiKeyLastUsed ? ` (last used: ${status.apiKeyLastUsed})` : ' (stored)';
    console.log(`API key:        ${status.apiKeyMasked}${lastUsed}`);
  } else {
    console.log(`API key:        No key configured`);
  }

  const dashboardUrl = `http://localhost:${status.port || 4242}/dashboard`;
  const offline = status.proxyRunning ? '' : '  [offline]';
  console.log(`Dashboard:      ${dashboardUrl}${offline}`);

  if (status.counters) {
    const c = status.counters;
    if (c.today) {
      console.log(`Requests today: ${c.today.requests || 0}  (${c.today.blocks || 0} blocked)`);
    }
    if (c.today && c.today.blocks > 0 && c.blocked_by_risk) {
      const r = c.blocked_by_risk;
      console.log(`Blocks today:   ${c.today.blocks}    (CRITICAL: ${r.CRITICAL || 0}, HIGH: ${r.HIGH || 0})`);
    }
  }

  console.log(`${'─'.repeat(W + 4)}`);

  if (!status.proxyRunning) {
    console.log('');
    console.log('Start OnGarde: npx @ongarde/openclaw start');
  }
}

module.exports = {
  printBrandHeader,
  ok,
  err,
  spin,
  detail,
  warn,
  info,
  blank,
  separator,
  renderAhaMoment,
  renderStatus,
};
