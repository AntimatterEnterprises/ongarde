# OnGarde Diagnostic Tools

This directory contains diagnostic and setup tools for OnGarde integration with OpenClaw.

## openclaw-diagnostic.sh

Comprehensive diagnostic script that probes an OpenClaw installation to gather all information needed for OnGarde integration.

### Usage

**On your OpenClaw VPS/server:**

```bash
# Download the script
curl -O https://raw.githubusercontent.com/AntimatterEnterprises/ongarde/main/tools/openclaw-diagnostic.sh

# Make it executable
chmod +x openclaw-diagnostic.sh

# Run and save output
bash openclaw-diagnostic.sh > openclaw-report.txt

# View the report
cat openclaw-report.txt

# Or send it to the team
cat openclaw-report.txt | nc termbin.com 9999  # Creates shareable link
```

**Or if you have the repo cloned:**

```bash
cd ongarde/tools
bash openclaw-diagnostic.sh > ~/openclaw-report.txt
```

### What It Collects

1. **System Information**
   - OS version and kernel
   - Current user and permissions
   - System uptime

2. **OpenClaw Installation**
   - Binary location and version
   - Data directory structure
   - Installed components

3. **Configuration**
   - openclaw.json (sanitized - secrets redacted)
   - Environment variables (sanitized)
   - Model provider configurations

4. **Running Services**
   - OpenClaw processes
   - Gateway status
   - Port bindings

5. **Network Setup**
   - Listening ports
   - Routing configuration
   - Proxy settings

6. **Skills & Extensions**
   - Installed skills structure
   - Extension/plugin inventory
   - Sample configurations

7. **Integration Readiness**
   - Prerequisites check (Node.js, Python, npm)
   - Port availability for OnGarde proxy
   - Configuration modification access

8. **API Call Flow Analysis**
   - Current baseUrl configurations
   - Provider routing patterns
   - Gateway connectivity

### Security Notes

- **All secrets are automatically redacted** (API keys, tokens, passwords)
- The script is read-only (makes no changes to your system)
- No sensitive data is collected
- Safe to share the output with the development team

### What We Learn From This

The diagnostic helps us understand:

1. **Where OpenClaw is installed** - File paths and structure
2. **How it's configured** - Current provider setup
3. **What's running** - Active processes and ports
4. **Network topology** - VM/VPS routing, if applicable
5. **Integration points** - Where to hook OnGarde in
6. **Prerequisites** - What's already installed vs. what we need

### Output Format

The report is structured in 18 sections, each focusing on a specific aspect of the OpenClaw installation. All sections are clearly labeled and easy to navigate.

### Troubleshooting

**Script won't run:**
```bash
# Try with bash explicitly
bash openclaw-diagnostic.sh

# Check permissions
ls -l openclaw-diagnostic.sh
chmod +x openclaw-diagnostic.sh
```

**Command not found errors:**
Some commands (like `netstat`) may not be available on all systems. The script handles this gracefully and uses alternatives where possible.

**Permission errors:**
The script should run with normal user permissions. If you see permission errors accessing OpenClaw files, you may need to run as the OpenClaw user:
```bash
sudo -u openclaw bash openclaw-diagnostic.sh > report.txt
```

### After Running

1. Review the report locally first
2. Check for any accidentally included sensitive information
3. Share the report with the OnGarde team
4. Wait for analysis and integration recommendations

---

**Last Updated:** February 17, 2026  
**Version:** 1.0.0
