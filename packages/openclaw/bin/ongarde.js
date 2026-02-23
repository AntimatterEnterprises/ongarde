#!/usr/bin/env node
'use strict';
/**
 * OnGarde CLI entry point.
 * Subcommand dispatch: init, start, status, rollback, uninstall.
 * E-007-S-001: package scaffold + CLI routing.
 */

// ─── Node version check ────────────────────────────────────────────────────────
const [major] = process.versions.node.split('.').map(Number);
if (major < 18) {
  console.error(
    `  ✗ OnGarde requires Node.js 18 or later.\n` +
    `    Current version: ${process.version}\n` +
    `    Download: https://nodejs.org/`
  );
  process.exit(1);
}

// ─── Argument parsing ─────────────────────────────────────────────────────────
const args = process.argv.slice(2);
const command = args[0];

function parseArgs(args) {
  const opts = {
    port: 4242,
    config: null,
    yes: false,
    json: false,
    help: false,
  };

  for (let i = 1; i < args.length; i++) {
    const arg = args[i];
    if (arg === '--port' || arg === '-p') {
      opts.port = args[++i];
    } else if (arg === '--config' || arg === '-c') {
      opts.config = args[++i];
    } else if (arg === '--yes' || arg === '-y') {
      opts.yes = true;
    } else if (arg === '--json') {
      opts.json = true;
    } else if (arg === '--help' || arg === '-h') {
      opts.help = true;
    }
  }

  return opts;
}

function printHelp() {
  console.log(`
OnGarde CLI — Runtime AI Security

Usage: npx @ongarde/openclaw <command> [options]

Commands:
  init       Run the 4-step onboarding wizard
  start      Start the OnGarde proxy and wait for it to be ready
  status     Show current proxy status, scanner health, and key info
  rollback   Restore your original OpenClaw config (stop proxy)
  uninstall  Fully remove OnGarde (stop proxy, restore config, uninstall package)

Options:
  --port <N>      Use port N instead of 4242 (default)
  --config <path> OpenClaw config file path (overrides auto-detection)
  --yes, -y       Non-interactive mode (skip confirmations)
  --json          Output JSON (status command)
  --help, -h      Show this help

Examples:
  npx @ongarde/openclaw init
  npx @ongarde/openclaw init --config ~/.openclaw/config.json
  npx @ongarde/openclaw start --port 8080
  npx @ongarde/openclaw status --json
  npx @ongarde/openclaw rollback

Documentation: https://ongarde.io/docs
`);
}

// ─── Main dispatch ────────────────────────────────────────────────────────────
async function main() {
  const opts = parseArgs(args);

  if (!command || command === '--help' || command === '-h' || opts.help) {
    printHelp();
    process.exit(0);
  }

  let result;

  switch (command) {
    case 'init': {
      const { runInit } = require('../src/commands/init');
      result = await runInit({
        port: opts.port,
        config: opts.config,
        yes: opts.yes,
      });
      process.exit(result.success ? 0 : 1);
      break;
    }

    case 'start': {
      const { runStart } = require('../src/commands/start');
      result = await runStart({
        port: opts.port,
        config: opts.config,
      });
      process.exit(result.success ? 0 : 1);
      break;
    }

    case 'status': {
      const { runStatus } = require('../src/commands/status');
      result = await runStatus({
        port: opts.port,
        json: opts.json,
      });
      process.exit(result.exitCode);
      break;
    }

    case 'rollback': {
      const { runRollback } = require('../src/commands/rollback');
      result = await runRollback();
      process.exit(result.success ? 0 : 1);
      break;
    }

    case 'uninstall': {
      const { runUninstall } = require('../src/commands/uninstall');
      result = await runUninstall();
      process.exit(result.success ? 0 : 1);
      break;
    }

    default: {
      console.error(`  ✗ Unknown command: ${command}`);
      console.error(`    Run 'npx @ongarde/openclaw --help' for usage.`);
      process.exit(1);
    }
  }
}

main().catch((err) => {
  console.error(`  ✗ Unexpected error: ${err.message}`);
  if (process.env.ONGARDE_DEBUG) console.error(err.stack);
  process.exit(1);
});
