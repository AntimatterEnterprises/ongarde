# OnGarde Setup Generator

A browser-based tool that generates a customized OnGarde + OpenClaw installation script
for Ubuntu servers (e.g. DigitalOcean Droplets).

## What It Is

`index.html` is a standalone HTML/JS page. Users fill in their configuration (VPS hostname,
API keys, ports, agent settings) and the page generates a ready-to-run bash script that:

1. Installs Python 3.12+, Node.js, and OnGarde dependencies
2. Clones and installs the OnGarde proxy
3. Installs OpenClaw and the `@ongarde/openclaw` CLI
4. Configures OnGarde to start on boot
5. Runs the OnGarde onboarding wizard

## Status

**POC v1** — Ubuntu-only, client-side script generation. No server-side component.

This tool is included in the repo for transparency and community contribution.
Feature backlog is in `BACKLOG.md`. UX assessment is in `UX-ASSESSMENT.md`.

## Usage

Open `index.html` directly in a browser (no server required) or host it statically.

## Files

| File | Description |
|------|-------------|
| `index.html` | Main setup generator UI and script generator logic |
