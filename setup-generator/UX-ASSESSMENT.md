# UX Assessment: OnGarde Quick Start Setup Generator

**Assessor:** Mary, Business Analyst  
**Date:** 2026-02-25  
**Subject:** `/ongarde/setup-generator/index.html` — POC v1  
**Audience:** Development team  

---

## Executive Summary

The Setup Generator is well-engineered and visually polished, but it's currently optimized for the person who _built_ it, not the person who _needs_ it. The form exposes every configuration knob simultaneously — a coherent mental model for a DevOps engineer, and a wall of jargon for a founder or PM who just bought their first VPS. **The CEO's concern is validated.** Without changes, non-technical users will hit friction in sections 2, 3, and 5 and likely abandon the tool or produce a broken script.

The good news: the script generation logic and defaults are largely sound. This is a progressive disclosure problem, not an architecture problem.

---

## 1. User Persona Fit

### Who Is This Actually For?

The current form implicitly targets a **Linux-fluent DevOps engineer** who:
- Understands SSH auth methods and key vs. password trade-offs
- Knows what a port is and when to change one
- Has opinions about `localhost` vs. LAN binding
- Is comfortable editing YAML config files post-install

The stated target user is broader:

| Persona | Comfort Level | Will Struggle With |
|---------|--------------|-------------------|
| Self-hosted AI Developer | High | Nothing — this form works for them |
| Non-engineer Founder/PM | Low | SSH section, all ports, Gateway Bind, SSH Hardening warning |
| Technical-ish Enthusiast | Medium | OpenClaw Gateway Bind, SSH Hardening, port conflicts |

**Assessment:** The form is appropriate for Persona 1 and functional but friction-heavy for Persona 2 and 3. Since the product's value prop is "get from zero to working AI stack in one session," Persona 2 is the highest-value user to unblock. They are also the most likely to bounce.

### Is the Complexity Appropriate?

**No — not all at once.** Every field in the current form is justified in isolation, but presenting all five sections simultaneously creates cognitive overload. A first-time user has no mental model for what OnGarde, OpenClaw, and BMad are yet, let alone why they'd change port `18789`.

---

## 2. Friction Points — Specific Fields That Cause Drop-Off

These are listed in order of severity.

### 🔴 Critical Friction

**`OpenClaw Gateway Bind` (Services & Security section)**  
```
LAN (all interfaces — VPS access)
Loopback (localhost only)
Auto (detect best interface)
```
This is the most confusing field in the form. A non-technical user will have no idea what "bind" means, what "all interfaces" implies for security, or why loopback would be wrong for their setup. The label "OpenClaw Gateway Bind" is internal developer vocabulary. For a VPS deployment (the stated use case), `LAN` is always correct. This field should not be visible to basic users.

---

**`Harden SSH` checkbox with `⚠ Key Auth Required` badge**

The warning badge is correctly placed but incompletely explained. A user who checks this box without having SSH key auth set up _will lock themselves out of their server_. The badge communicates "danger" without communicating _why_ or _what happens_. A non-technical user either (a) ignores it and checks the box anyway, or (b) is scared off from enabling a legitimate security feature. Either outcome is bad.

---

**`SSH Port` number field (SSH Access section)**

Showing a port number field implies users should consider changing it. Most VPS providers use port 22. For users who don't know what SSH port security through obscurity means, this is a false choice that adds noise. The field also has no explanation of when or why to change it.

---

### 🟡 Significant Friction

**`Auth Method` radio group — "🔑 Password" vs "📄 SSH Key"**

The field label "Auth Method" is developer-speak. The real question for a first-time user is: _"How do you currently log into your VPS?"_ Many new VPS owners don't know if they have an SSH key or not — they may have just typed a root password in their VPS provider's web console. When they select "SSH Key," the `Public Key (for authorized_keys)` textarea appears with a placeholder of `ssh-ed25519 AAAA...` — which will be completely opaque to them.

---

**Port number triplet — `OnGarde Port`, `OpenClaw Port`, `File Browser Port`**

Three port fields in a row. 4242, 18789, 8080. The overwhelming majority of users will (and should) use the defaults. Showing these fields at the top level implies they need to make a decision here. There is no hint text explaining when you'd need to change a port (e.g., port conflicts with existing software). These three fields belong in an "Advanced" section.

---

**`SSH User` placeholder "root"**

The placeholder `root` is correct for most VPS providers, but some use `ubuntu`, `debian`, or a custom username. The field shows empty, with "root" as a ghost placeholder. Many users will leave it blank assuming the default is fine — and it is, because the script falls back to `root`. But the field's emptiness alongside the placeholder creates ambiguity. Pre-fill it with `root`.

---

**`VPS Public IP` — no format hint or validation**

This is one of the most essential fields — it determines the access URLs in the completion card. There is no validation and the placeholder (`203.0.113.10`) is an RFC 5737 documentation address that looks real. Users may not realize they need to replace it with their actual IP, or they may get it from the wrong place (e.g., copying a private IP from their VPS dashboard).

---

### 🟢 Minor Friction

**`BMad Agents` section — name input fields on every row**

The ability to rename agents (Mary → anything) is a power-user customization feature. It's useful for teams that want to name their agents differently. For a first-time setup, it adds visual noise and implies a decision ("do I need to rename these?"). Default names are sensible. These inputs should either be hidden behind an "Advanced / Customize" toggle or removed from the v1 UI.

**`Hostname` field is empty (not pre-filled)**

The default value `ongarde-server` is in the placeholder only. The script correctly falls back to this value, but the empty field suggests the user needs to enter something. Pre-fill it.

**`+ Add Provider` button placement**

The `+ Add Provider` button sits below OpenAI Key at the bottom of the API Keys section. For non-technical users unfamiliar with the concept of "providers" in this context (LLM API providers), the button label is ambiguous. Consider: `+ Add Another AI Provider (Gemini, Mistral, etc.)`.

**`POC v1` header badge**

This badge signals "experimental / unfinished" to a savvy user. For a non-technical founder evaluating the product, it's a trust signal in the wrong direction. Consider removing or replacing with something like `Quick Start`.

---

## 3. Smart Defaults Audit

The core defaults are good. The problem is that defaults buried in visible form fields invite unnecessary second-guessing.

| Field | Current Default | Assessment |
|-------|----------------|------------|
| OS Version | Ubuntu 24.04 LTS | ✅ Correct |
| Hostname | (empty, placeholder: ongarde-server) | ⚠ Pre-fill with `ongarde-server` |
| VPS Public IP | (empty) | ✅ Correct — must be user-supplied |
| Timezone | UTC | ⚠ Consider browser-based auto-detect |
| SSH User | (empty, placeholder: root) | ⚠ Pre-fill with `root` |
| SSH Port | 22 | ✅ Correct — hide from basic view |
| SSH Auth Method | Password | ✅ Correct for new VPS owners |
| OnGarde Port | 4242 | ✅ Move to Advanced |
| OpenClaw Port | 18789 | ✅ Move to Advanced |
| File Browser Port | 8080 | ⚠ Move to Advanced; `8080` conflicts with many dev environments |
| OpenClaw Gateway Bind | LAN | ✅ Correct for VPS — hide from basic view |
| UFW Firewall | ✅ checked | ✅ Correct |
| fail2ban | ✅ checked | ✅ Correct |
| Harden SSH | ☐ unchecked | ✅ Correct — too dangerous to default-on |
| Auto Security Updates | ✅ checked | ✅ Correct |
| Agent roster (6 core) | ✅ checked | ✅ Correct |

**Fields that should just work without asking the user:**
- All three port fields (OnGarde, OpenClaw, File Browser)
- SSH Port
- SSH User
- OpenClaw Gateway Bind
- Agent name inputs (pre-filled names work fine)

**Fields the user genuinely needs to fill in:**
- VPS Public IP
- At least one API Key (Anthropic or OpenAI)
- SSH Auth Method (if they know they have SSH key auth set up)

---

## 4. Progressive Disclosure Recommendation

The form should be restructured into three tiers:

### Tier 1 — Always Visible (The 20% that matters)

These are the fields the user _must_ provide or _will_ want to confirm:

- VPS Public IP
- Anthropic API Key
- OpenAI API Key
- Timezone (auto-detected if possible)
- SSH Auth Method (simplified language: "How do you log into your VPS?")
- Download script button (large, prominent, always visible)

### Tier 2 — One Click to Reveal ("Customize")

These are reasonable choices that power users may want to adjust:

- Hostname
- OS Version
- SSH Port / SSH User
- BMad Agent selection (checkboxes only, no name editing)
- Extra AI Providers
- Hardening options (UFW, fail2ban, Auto Updates, Harden SSH — with better explainers)

### Tier 3 — "Advanced / Developer Settings" (Collapsed by Default)

- All port numbers
- OpenClaw Gateway Bind
- Agent name customization
- Agent file path references

---

## 5. Recommended v2 UX Structure

### Proposed Layout

```
┌─────────────────────────────────────────────────────────┐
│  🤺 OnGarde Setup Generator        [Quick Start]        │
└─────────────────────────────────────────────────────────┘

┌─ LEFT PANEL (narrower: ~400px) ────────────────────────┐
│                                                          │
│  ── Step 1: Your VPS ─────────────────────────────────  │
│  VPS Public IP  [                  ]                     │
│  Timezone       [auto-detected ▼  ]                     │
│                                                          │
│  ── Step 2: AI Provider Keys ─────────────────────────  │
│  Anthropic Key  [sk-ant-…         ]                     │
│  OpenAI Key     [sk-…             ]                     │
│  [+ Add another provider]                               │
│                                                          │
│  ── Step 3: SSH Access ───────────────────────────────  │
│  How do you log into your VPS?                          │
│  [ Password Login ]  [ SSH Key ]                        │
│  Password: [••••••••] (or SSH public key textarea)      │
│                                                          │
│  ─────── [ ▸ Customize Agents & Settings ] ───────────  │
│  (collapsed by default — expands to:)                   │
│   • BMad Agents (checkbox list, no name inputs)         │
│   • Security options (UFW, fail2ban, Auto Updates,      │
│     Harden SSH — with plain-English descriptions)       │
│   • Hostname / OS Version                               │
│   ─── [ ▸ Advanced / Developer Settings ] ────────────  │
│   (collapsed — port numbers, Gateway Bind, name edits)  │
│                                                          │
└──────────────────────────────────────────────────────────┘

┌─ RIGHT PANEL (script preview) ────────────────────────┐
│  Generated Script  (280 lines)   [📋 Copy] [⬇ Download] │
│  ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│  #!/usr/bin/env bash                                    │
│  # OnGarde Quick Start Installer                        │
│  ...                                                    │
└──────────────────────────────────────────────────────────┘
```

### Reasoning

- **"Step 1 / Step 2 / Step 3" labeling** removes the "where do I start?" paralysis. The user knows there are 3 things to do.
- **IP + Timezone** are grouped as "Your VPS" because they relate to the machine, not the software.
- **API Keys** are surfaced prominently because they are the #1 reason the install works or doesn't.
- **SSH Auth** is a single, plainly-worded choice. Most users will select "Password Login" and move on.
- **Everything else defaults to sensible values** and is accessible but not imposing.
- **The "Customize" accordion** gives power users what they need without front-loading it on everyone.
- **The "Advanced / Developer Settings" accordion** within the customize section is a secondary collapse for ports and binding — making it a two-click journey for the values that should almost never change.

---

## 6. Quick Wins

These changes can be made immediately without a structural redesign. Each is a targeted edit to the existing HTML.

---

### Quick Win 1: Pre-fill `hostname` and `sshUser` fields

**Current behavior:** Both fields display placeholder text but are empty. The script falls back to defaults (`ongarde-server`, `root`) correctly, but the empty state creates user uncertainty.

**Fix:** Change the HTML `input` elements to include `value` attributes:

```html
<!-- Before -->
<input type="text" id="hostname" placeholder="ongarde-server" oninput="generate()" />
<input type="text" id="sshUser" placeholder="root" oninput="generate()" />

<!-- After -->
<input type="text" id="hostname" value="ongarde-server" oninput="generate()" />
<input type="text" id="sshUser" value="root" oninput="generate()" />
```

**Impact:** Eliminates "do I need to fill this in?" uncertainty. Low risk — values are still editable.

---

### Quick Win 2: Move all three port fields into the existing collapsed structure

**Current:** `OnGarde Port`, `OpenClaw Port`, and `File Browser Port` are in the "Services & Security" section body — always visible.

**Fix:** Wrap the port field row in a new inner section that starts collapsed:

```html
<!-- Replace the existing field-row for ports with: -->
<details style="margin-bottom:12px">
  <summary style="cursor:pointer; font-size:11px; color:var(--text-muted); font-weight:600; text-transform:uppercase; letter-spacing:0.5px;">
    ▸ Port Numbers (Advanced)
  </summary>
  <div style="margin-top:8px">
    <div class="field-row">
      <!-- existing port fields -->
    </div>
    <div class="field" style="margin-top:4px">
      <!-- OpenClaw Gateway Bind field -->
    </div>
  </div>
</details>
```

This also naturally buries `OpenClaw Gateway Bind` in the same collapsed block.

**Impact:** Removes 4 confusing fields from the default view. High impact, minimal code change.

---

### Quick Win 3: Replace the "Harden SSH" warning badge with inline explanatory text

**Current:**
```html
<span class="check-label">Harden SSH</span>
<span class="warn-badge">⚠ Key Auth Required</span>
```

The badge warns but doesn't explain the consequence.

**Fix:**
```html
<span class="check-label">Harden SSH — Disable Password Login</span>
```
And add below the checkbox row:
```html
<div class="field-hint" style="margin-left:23px; color:var(--yellow);">
  ⚠ Only enable if you've set up SSH key access. Password SSH login will be permanently disabled — you can be locked out if misconfigured.
</div>
```

**Impact:** Users who don't understand SSH hardening will correctly leave this unchecked. Users who do understand it get confirmation. Eliminates a potential "lock yourself out" incident.

---

### Quick Win 4: Rename "Auth Method" and simplify the SSH section label

**Current label:** `Auth Method` (developer terminology)

**Fix:**
```html
<!-- Before -->
<label>Auth Method</label>

<!-- After -->
<label>How do you log into your VPS?</label>
```

And rename the radio buttons:
```html
<!-- Before -->
🔑 Password    📄 SSH Key

<!-- After -->
🔑 Password    🗝️ SSH Key File
```

For the SSH Key textarea, change the label from:
```
Public Key (for authorized_keys)
```
to:
```
Your SSH Public Key (paste from ~/.ssh/id_ed25519.pub or id_rsa.pub)
```

**Impact:** Removes jargon from the most technically intimidating section. Non-technical users understand "how do you log in?" immediately.

---

### Quick Win 5: Add a "minimum viable" indicator to the form

**Problem:** Users don't know which fields are required vs. optional. They may spend time agonizing over every field when only 2-3 actually matter.

**Fix:** Add a subtle info bar at the top of the config panel:

```html
<div style="background:rgba(78,158,255,0.08); border:1px solid rgba(78,158,255,0.2); 
            border-radius:6px; padding:10px 14px; margin:12px 20px 0; font-size:12px; 
            color:var(--blue);">
  💡 <strong>Just getting started?</strong> Fill in your VPS IP and at least one API key — everything else has smart defaults.
</div>
```

**Impact:** Dramatically reduces cognitive load for first-time users. The form stops feeling like a form and starts feeling like a checklist with one mandatory item.

---

## Summary Table

| Issue | Severity | Effort | Quick Win? |
|-------|----------|--------|-----------|
| OpenClaw Gateway Bind visible by default | 🔴 High | Low | ✅ (Win 2) |
| Harden SSH warning unexplained | 🔴 High | Low | ✅ (Win 3) |
| Port fields visible by default | 🟡 Medium | Low | ✅ (Win 2) |
| SSH Auth Method uses jargon | 🟡 Medium | Low | ✅ (Win 4) |
| Hostname / sshUser not pre-filled | 🟡 Medium | Trivial | ✅ (Win 1) |
| No "required fields" guidance | 🟡 Medium | Low | ✅ (Win 5) |
| Agent name inputs always visible | 🟢 Low | Low | No (v2) |
| POC v1 badge undermines trust | 🟢 Low | Trivial | Remove it |
| Add Provider label ambiguous | 🟢 Low | Trivial | Rename it |
| No IP validation | 🟢 Low | Medium | No (v2) |

---

*Document prepared for OnGarde development team. Questions → spawn Mary.*
