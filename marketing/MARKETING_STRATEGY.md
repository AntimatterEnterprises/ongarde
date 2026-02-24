# OnGarde Autonomous Marketing Strategy
**Version:** 1.0  
**Date:** February 2026  
**Prepared for:** CEO Review & Implementation  
**Status:** Ready for Execution

---

## Executive Summary

OnGarde enters a **perfectly timed market** with a **differentiated product** and **zero direct open-source competition**. The target audience (self-hosted AI agent developers) is technically sophisticated, community-driven, and deeply skeptical of marketing fluff — they respond to demos, code, honest explanations of problems solved, and peer validation.

**Core marketing thesis:** The audience won't be persuaded by ads. They'll be won by showing up where they already are, speaking their language, and making OnGarde the obvious answer to a problem they already feel.

**Primary goal (Month 1–2):** Acquire first 500 free users from the OpenClaw community, validate conversion funnel, establish brand presence.

**Secondary goal (Month 2–4):** Expand to Agent Zero, CrewAI, and LangChain communities. Begin building B2B pipeline (Reyes persona — small team leads).

**Autonomous agent role:** OpenClaw handles monitoring, tier-1 response, content scheduling, and lead capture. Human-in-the-loop handles content creation, community engagement, and all sales conversations.

---

## 1. Channel Strategy

### Priority Tier 1: Quick Wins (Week 1–2, Do These First)

These channels directly reach the existing addressable audience with minimal setup cost.

---

#### 1.1 OpenClaw Community — Direct Insertion ⚡ HIGHEST IMPACT

**Why first:** 14K+ engaged users already using the exact platform OnGarde integrates with. Zero acquisition cost. Perfect product-market fit signal.

**Actions:**
- Post a **"Show HN"-style announcement** in the OpenClaw community Discord/forums: "I built runtime content security for OpenClaw — one config line, parries credential leaks in <50ms"
- Submit to the **OpenClaw plugin/integration registry** (if one exists) or GitHub Discussions
- Create a GitHub issue in the OpenClaw repo referencing the integration (if appropriate + non-spammy)
- **Automated:** OpenClaw agent monitors `#plugins`, `#security`, `#showcase` channels for relevant keywords and surfaces to CEO

**What to post:** A real demo. Show a before/after: agent sends a message containing an API key → OnGarde redacts it → clean request reaches LLM. 60-second screen recording.

**Human required:** CEO writes the initial announcement post. Authentic first-person voice. Not marketing copy.

---

#### 1.2 GitHub — The Developer's Front Door ⚡ CRITICAL

**Why:** GitHub IS the distribution channel for developer tools. Stars = social proof. README = sales page.

**Actions:**
- **README overhaul:** Must include animated GIF/screenshot showing interception in action. One-line install. Copy that reads: "One line of config. Parries credential leaks, prompt injection, and dangerous commands in <50ms."
- **GitHub Releases:** Every release gets a proper release note (not just a changelog). Short, benefit-focused, shareable.
- **GitHub Topics:** Add: `ai-security`, `llm-security`, `prompt-injection`, `agent-security`, `openclawn`, `crewai`, `langchain`, `runtime-security`
- **Issue templates:** Replace the current "contact us via GitHub issues" approach entirely (see Section 2)
- **Automated:** `gh-issues` skill monitors for mentions of OnGarde across GitHub. Agent responds to integration questions with helpful answers + link to docs.

**Metric:** 500 GitHub stars in first 30 days = healthy signal.

---

#### 1.3 Twitter/X — Real-Time Developer Discourse ⚡ HIGH IMPACT

**Why:** The AI/dev community is extremely active on X. This is where "build in public" wins audiences.

**Account:** `@OnGardeAI` or `@OnGardeSecurity` (check availability)

**Bio:** `Épée-grade runtime security for AI agents. Parries credential leaks, prompt injection, and dangerous commands in <50ms. Always en garde. 🤺`

**Strategy — Build in Public:**
- CEO tweets raw building moments: "Just blocked our 1,000th prompt injection attempt in testing. Here's what it looked like →"
- Share interesting threat patterns found during testing (anonymized, technical)
- Engage with every mention of: `#AIAgentSecurity`, `#PromptInjection`, `#LLMSecurity`, `#OpenClaw`, `#CrewAI`

**Tweet formula that works for dev tools:**
1. Problem tweet: "Every self-hosted agent I've tested leaks credentials at some point. Here's why it happens →"
2. Demo tweet: Short screen recording, no voiceover, visible result
3. Insight tweet: "Prompt injection attacks we've seen this week — thread 🧵"
4. CTA tweet (rare, ~1 in 10): "Built this. It's free. One line of config."

**Automated:** Agent monitors X mentions of `ongarde`, `@OnGardeAI`, and competitor/keyword mentions. Escalates to CEO for response. Does NOT auto-reply on X (too risky for brand).

**Posting cadence:** 3–5 tweets/week. CEO writes these. Agent can draft, CEO approves.

---

#### 1.4 Reddit — Community Credibility ⚡ HIGH VALUE, HIGH RISK

**Target subreddits:**
- `r/LocalLLaMA` (150K+) — self-hosters, technically deep
- `r/MachineLearning` (2M+) — researchers and practitioners
- `r/AIAssistants` — agent users
- `r/netsec` — security audience discovering AI
- `r/ExperiencedDevs` — senior devs adopting AI agents
- `r/devops` — production systems operators

**Strategy:**
- **Never post promotional content directly.** Post educational content that mentions OnGarde organically.
- Lead with: "I analyzed 8 open-source agent platforms for runtime security gaps. Here's what I found (and what I built)" → thread with real data, comparison table, honest limitations. OnGarde is the answer at the end, not the headline.
- Answer questions about prompt injection / AI security with genuine help. Include OnGarde only when it's genuinely the right tool.
- One Reddit AMA in month 2: "I built runtime content security for self-hosted AI agents — AMA"

**Human required:** All Reddit posts and comments. Reddit communities immediately detect and destroy inauthenticity. No automation here.

**Automated:** Agent monitors r/LocalLLaMA, r/netsec, r/MachineLearning for keywords (`prompt injection`, `credential leak`, `agent security`, `LLM security`) and surfaces relevant threads to CEO for optional manual response.

---

### Priority Tier 2: Pipeline Building (Month 1–3)

#### 1.5 LinkedIn — B2B Sales Signal

**Why:** Reyes (small team lead) and Morgan (platform operator) are on LinkedIn. Alex (indie dev) is not.

**Account:** Company page `OnGarde Security` + CEO personal account posting

**Content that works on LinkedIn for this persona:**
- "We analyzed 15 AI agent platforms. None have runtime content security. Here's the gap." (infographic or carousel)
- "Why your AI agent's compliance posture has a critical gap" (thought leadership)
- Case study posts: "How we helped a team reduce credential exposure risk in their agent platform" (after first paying customers)
- The "compliance" and "audit trail" angles resonate most here

**Posting cadence:** 2–3x/week, company page. CEO personal page posts 1–2x/week.

**Automated:** Agent drafts LinkedIn posts from approved templates/themes. CEO edits and publishes manually (LinkedIn is relationship-driven — don't automate publishing).

---

#### 1.6 Hacker News — Credibility Multiplier

**Strategy:**
- **Month 1:** Submit OnGarde to "Show HN" — must be real, working product. Title matters enormously. Draft: "Show HN: OnGarde – Runtime content security proxy for self-hosted AI agents (parries prompt injection in <50ms)"
- **Month 2–3:** Submit high-quality technical post: "The runtime security gap in open-source AI agent platforms" — educational, data-backed, honest about limitations
- **Ongoing:** Engage in threads about AI security, prompt injection, agent safety. CEO answers with genuine depth.

**What makes HN work:** Being the person who built the thing, not a marketer. CEO must be the face here.

**Automated:** Agent monitors HN "who is hiring" for companies building with AI agents (future enterprise targets). Agent monitors `new.ycombinator.com` for relevant threads (prompt injection, agent security) and alerts CEO.

---

#### 1.7 DEV.to / Hashnode / Substack — Content Distribution

**Why:** SEO-friendly, developer-respected, free distribution.

**Content types:**
- Deep-dive technical posts: "How prompt injection actually works and how to stop it at runtime"
- Tutorial posts: "Add runtime security to your OpenClaw setup in 5 minutes"
- Architecture posts: "Proxy-based security: why sitting between your agent and LLM is the right pattern"

**Automated:** Agent cross-posts approved blog content to DEV.to and Hashnode. Monitors comments for questions and surfaces to CEO.

---

### Priority Tier 3: Ecosystem Expansion (Month 2–4)

#### 1.8 Agent Zero, CrewAI, LangChain Communities

**Approach:** Same as OpenClaw but staged — after OpenClaw integration is polished and testimonials exist.

- **Discord servers** for CrewAI, LangChain, Agent Zero — join as a user, contribute genuinely, announce integration when ready
- **GitHub Discussions** in each platform's repo
- **Platform-specific blog posts:** "How to add runtime security to your CrewAI crew in 60 seconds"

**Automated:** Agent monitors these Discord servers for security-related keywords and surfaces to CEO.

---

### What to AUTOMATE vs. What Needs Human Touch

| Task | Automate? | Why |
|------|-----------|-----|
| Monitor mentions across GitHub, Discord, Reddit, HN | ✅ Yes | Surveillance, not conversation |
| Surface relevant threads to CEO | ✅ Yes | Judgment call on response |
| Cross-post approved blog content | ✅ Yes | Deterministic, low-risk |
| Respond to GitHub issues (tier 1: FAQ, docs links) | ✅ Yes | High volume, low stakes |
| Respond to email inquiries (tier 1 auto-acknowledgement) | ✅ Yes | Speed matters |
| Draft social media posts for CEO approval | ✅ Yes | Saves CEO time |
| Lead capture + CRM entry | ✅ Yes | Data entry, not judgment |
| Posting to Twitter/X | ❌ No | Brand voice risk |
| Posting to Reddit | ❌ No | Community destroys inauthenticity |
| HN engagement | ❌ No | Community highly sensitive |
| LinkedIn engagement | ❌ No | Relationship-based |
| Sales conversations | ❌ No | Always human |
| Pricing conversations | ❌ No | Always human |

---

## 2. Inbound Contact Flow

### The Problem with GitHub Issues

GitHub issues signal: "we're an open-source project, not a company." For B2B SaaS, this:
- Leaks competitive intelligence (public issues)
- Creates poor first impression for paying prospects
- Makes sales conversations impossible to have properly
- Fails compliance-focused buyers (Reyes/Morgan) who need private conversations

### Recommended Contact Infrastructure (Minimum Viable)

#### Layer 1: Self-Service (Zero Human Touch)
- **Docs site** with comprehensive FAQ, integration guides, troubleshooting
- **Status page** (UptimeRobot or Better Stack — free tier)
- **GitHub Issues** — ONLY for confirmed bugs on open-source components. Not for questions, not for sales.

#### Layer 2: Community (Light Human Touch)
- **Discord community server** (not the OpenClaw server — OnGarde's own server)
  - Channels: `#general`, `#support`, `#integrations`, `#announcements`, `#showcase`
  - Agent monitors all channels, responds to tier-1 questions, escalates to CEO
  - Free users and indie devs (Alex persona) live here
  - This replaces GitHub issues for community support

#### Layer 3: Direct Contact (Human Touch)
- **Business email:** `hello@ongarde.io` — for all inbound inquiries
  - Auto-acknowledgement within 5 minutes (agent-handled)
  - CEO responds to genuine prospects within 24 hours
- **Sales email:** `sales@ongarde.io` (or route all to hello@)
- **Calendly link** — embedded on pricing page for Team/Enterprise tier: "Talk to us about your requirements"
  - 30-min discovery call for Team+ prospects
  - 60-min enterprise demo for Morgan persona

#### Layer 4: Enterprise (Full White-Glove)
- Email → Calendly → discovery call → custom proposal
- No web form required until traffic justifies it

### Contact Flow Diagram

```
User has a question/problem
       │
       ▼
  Docs site? ──→ YES → Self-serve (no human)
       │ NO
       ▼
  Free user? ──→ YES → Discord community → Agent (tier 1) or CEO (tier 2)
       │ NO (paid/prospect)
       ▼
  hello@ongarde.io ──→ Agent auto-ack → CEO response within 24h
       │ (Team/Enterprise inquiry)
       ▼
  Calendly booking ──→ CEO discovery call
```

### AI-Agent-Handled (Tier 1)

The agent CAN autonomously handle:
- FAQ responses (integration questions, pricing clarification, supported platforms)
- "Where do I find X in the docs?" questions
- GitHub issue triage: label, link to docs, confirm bug vs. feature request
- Email auto-acknowledgement with relevant doc links
- Lead qualification questions (company size, use case, platform) → capture to CRM

The agent MUST escalate to CEO:
- Any pricing negotiation
- Any enterprise/compliance inquiry
- Any complaint
- Any press or analyst inquiry
- Any partnership discussion
- Any security vulnerability report (IMMEDIATELY)

### GitHub Issues — New Policy

Replace the contact-via-issues flow with this in README and website:

```
🐛 Found a bug? → Open a GitHub Issue
💬 Questions + support → Join our Discord community
📧 Business inquiries → hello@ongarde.io
📅 Enterprise / Team demo → [Calendly link]
```

---

## 3. Autonomous Agent Architecture

### What the Marketing Agent Does

The OnGarde marketing agent is a **surveillance + drafting + tier-1 response** system. It is NOT a publishing bot. Every piece of outbound communication that touches the brand goes through CEO approval except explicitly defined exceptions.

### Monitoring Stack (Always-On)

```yaml
# OnGarde Marketing Agent — Monitoring Config

watch:
  github:
    skill: gh-issues
    targets:
      - repo: ongarde/ongarde (own repo — all activity)
      - search: "prompt injection" language:any (new repos/issues)
      - search: "agent security llm" language:any
      - search: "OnGarde" (brand mentions)
    actions:
      - new_issue: auto-label + tier-1 response if FAQ match
      - new_star: log to metrics
      - security_report: IMMEDIATE escalate to CEO

  discord:
    skill: discord
    servers:
      - ongarde-community (own server — all channels)
      - openclaw-server (monitor: #plugins, #security, #showcase)
    keywords:
      - "prompt injection"
      - "credential leak"
      - "OnGarde"
      - "security proxy"
      - "ongarde.io"
    actions:
      - keyword_hit: surface to CEO daily digest
      - direct_question_own_server: tier-1 auto-response if FAQ match

  reddit:
    skill: blogwatcher (RSS)
    subreddits:
      - r/LocalLLaMA
      - r/netsec
      - r/MachineLearning
      - r/ExperiencedDevs
    keywords:
      - "prompt injection"
      - "LLM security"
      - "agent security"
      - "credential leak agent"
      - "OnGarde"
    actions:
      - keyword_hit: surface to CEO daily digest

  hackernews:
    skill: blogwatcher (RSS)
    feeds:
      - https://hnrss.org/newest?q=prompt+injection
      - https://hnrss.org/newest?q=agent+security
      - https://hnrss.org/newest?q=LLM+security
    actions:
      - new_thread: surface to CEO if relevance score high

  web:
    skill: web_search (periodic)
    queries:
      - '"ongarde.io"'
      - '"OnGarde" AI security'
      - "prompt injection detection tool site:github.com"
    schedule: daily 09:00 UTC
    actions:
      - new_result: add to CEO digest

  email:
    skill: himalaya
    inbox: hello@ongarde.io
    actions:
      - new_email: auto-acknowledge within 5min
      - classify_lead: extract company, use case, platform → log to CRM sheet
      - escalate: forward to CEO with classification summary

respond_autonomously:
  - github_faq: pattern-match questions → link to doc section
  - discord_faq: same
  - email_ack: "Thanks for reaching out. We'll reply within 24 hours. In the meantime: [relevant doc link]"

draft_for_approval:
  - weekly_twitter_drafts: 3–5 tweet drafts → CEO Slack/Discord DM for approval
  - weekly_linkedin_draft: 1–2 post drafts → CEO approval
  - blog_post_outline: when interesting threat pattern detected → suggest topic to CEO
  - release_announcement: draft from changelog → CEO edits + publishes

escalate_immediately:
  - security_vulnerability_report: any channel
  - press_inquiry: any channel
  - enterprise_deal_signal: company with 10+ users or explicit enterprise mention
  - negative_press_or_community_backlash
  - partnership_inquiry
```

### Metrics the Agent Tracks Weekly

- GitHub stars (delta)
- Discord members (delta)
- Website traffic (via Vercel analytics or Plausible)
- Email inquiries (count, classification)
- Keyword mentions (volume, sentiment)
- Free → paid conversion signals

Agent produces a **weekly digest** (Sunday evening) delivered to CEO: "Here's the week in OnGarde marketing. X mentions, Y leads, Z things need your attention."

---

## 4. Content Strategy

### What Resonates with Developer + Security Audiences

Developers and security practitioners share a common trait: they have very high bullshit detectors. They respond to:

1. **Raw technical demos** — "Here's a real attack. Here's how we stopped it."
2. **Honest problem framing** — "Here's why this is hard, and here's our current answer"
3. **Educational deep-dives** — "How prompt injection actually works (and why most defenses fail)"
4. **Behind-the-scenes building** — "We built this in public. Here's what we learned."
5. **Comparisons with integrity** — Include limitations, not just strengths

They do NOT respond to:
- Marketing copy ("industry-leading solution")
- Vague claims without proof
- Pushy CTAs
- Content that feels like an ad

### Content Pillars

**Pillar 1: Technical Education (40% of content)**
- Explain the threat landscape honestly and in depth
- "What is prompt injection and how does it work?"
- "The anatomy of a credential leak in AI agents"
- "Runtime vs. configuration security — what's the difference and why does it matter?"

**Pillar 2: Product Demos (30% of content)**
- Screen recordings of real interception events
- "We captured this in our test suite — here's what it looked like"
- Integration walkthroughs (OpenClaw, CrewAI, LangChain)
- Benchmark posts: latency measurements, throughput, overhead analysis

**Pillar 3: Build in Public (20% of content)**
- "Week 3 of OnGarde: Here's what we got right and what we got wrong"
- Release announcements with honest context
- Architecture decisions: "We chose to do X instead of Y because…"

**Pillar 4: Community / Ecosystem (10% of content)**
- Celebrate community integrations
- Amplify interesting security research by others
- "Interesting threat pattern we're seeing this week"

---

### Content Calendar: Launch Cadence (Weeks 1–4)

**WEEK 1 — Existence Establishment**

| Day | Channel | Content | Who |
|-----|---------|---------|-----|
| Mon | GitHub | Ship polished README with demo GIF, installation badge, platform compatibility table | Agent drafts, CEO reviews |
| Mon | Twitter/X | Launch tweet: "Just shipped OnGarde — runtime content security for self-hosted AI agents. One config line. Parries credential leaks in <50ms. 🤺 [link]" | CEO writes |
| Tue | OpenClaw Discord | Post in #showcase: authentic, first-person intro with demo | CEO writes |
| Wed | DEV.to | "How I built a runtime security proxy for AI agents (and why no one else has)" — technical, honest, educational | CEO writes |
| Thu | Twitter/X | Thread: "The security gap in every open-source AI agent platform (thread)" — data from market research | CEO writes |
| Fri | HN | Submit to Show HN | CEO |
| Sat | Twitter/X | Demo tweet: screen recording of credential interception | CEO with agent-drafted caption |

**WEEK 2 — Technical Depth**

| Day | Channel | Content | Who |
|-----|---------|---------|-----|
| Mon | Blog / Substack | "Prompt injection: how it works, why it's hard to stop, what we're doing about it" — 1500 word deep dive | CEO |
| Mon | Twitter/X | 3-tweet thread summarizing the blog post | Agent drafts, CEO edits |
| Tue | LinkedIn | "Why your AI agent's compliance posture has a gap — and how to close it in 60 seconds" | Agent drafts, CEO edits |
| Wed | Reddit (r/LocalLLaMA) | Share the blog post with genuine framing — "I built this and wrote up what I learned" | CEO |
| Thu | Twitter/X | "What I've learned building runtime security for AI agents this week" — build-in-public tweet | CEO |
| Fri | GitHub | Release v0.x with changelog and release notes | Agent drafts, CEO publishes |
| Sat | Discord | Community Q&A in own Discord server — invite OpenClaw community members | CEO |

**WEEK 3 — Platform Expansion**

| Day | Channel | Content | Who |
|-----|---------|---------|-----|
| Mon | Blog | "Add runtime security to CrewAI in 5 minutes" — integration tutorial | CEO |
| Mon | CrewAI Discord | Share tutorial authentically | CEO |
| Tue | Twitter/X | "OnGarde now supports CrewAI, Agent Zero, and LangChain. One proxy, universal protection." | CEO |
| Wed | LinkedIn | "Multi-agent security is not optional: why teams running CrewAI and LangGraph need runtime content scanning" | Agent drafts, CEO edits |
| Thu | DEV.to | Cross-post CrewAI tutorial | Agent |
| Fri | Twitter/X | Demo: "Blocked a prompt injection attempt in a multi-agent CrewAI setup. Here's the trace:" | CEO |

**WEEK 4 — Community + Social Proof**

| Day | Channel | Content | Who |
|-----|---------|---------|-----|
| Mon | Twitter/X | "Month 1 metrics — raw numbers, no spin" (stars, users, threats blocked in test) | CEO |
| Tue | HN | Submit technical post: "The runtime security gap in open-source AI agent platforms" | CEO |
| Wed | Blog | "One month building OnGarde: what we shipped, what we learned, what's next" | CEO |
| Thu | LinkedIn | "What our first 100 users taught us about AI agent security" | CEO |
| Fri | Twitter/X | Showcase: community member or early user using OnGarde | CEO (with permission) |
| Sat | Discord | Feature request / roadmap discussion with community | CEO |

---

### SEO Strategy

**Target Keyword Clusters:**

**Cluster 1: Prompt Injection (High intent, growing)**
- `prompt injection detection`
- `prompt injection prevention tool`
- `prompt injection protection for LLMs`
- `how to stop prompt injection`
- `LLM prompt injection defense`

**Cluster 2: AI Agent Security (Emerging, land early)**
- `AI agent security`
- `AI agent runtime security`
- `agentic AI security`
- `self-hosted AI agent security`
- `open source AI agent security`

**Cluster 3: LLM Content Security (Broader)**
- `LLM content filtering`
- `LLM output filtering`
- `LLM security proxy`
- `LLM API proxy security`
- `runtime LLM security`

**Cluster 4: Credential/PII Protection (Pain-specific)**
- `prevent AI credential leak`
- `LLM credential exposure`
- `AI agent PII protection`
- `redact PII from LLM requests`

**SEO Tactics:**
- Create dedicated landing pages for each cluster (e.g., `/prompt-injection-protection`, `/ai-agent-security`, `/llm-content-filtering`)
- Every blog post targets one specific long-tail keyword
- Use schema markup for product pages
- Secure backlinks from: platform repos (README mentions), security community blogs, DEV.to canonical posts
- Internal linking: every technical post links to relevant docs and pricing

**Quick win:** Write "The Complete Guide to Prompt Injection in AI Agents" — 3000+ words, comprehensive, targets the cluster head term. This becomes the pillar content linked from everything else.

---

## 5. Immediate Action Items — This Week

Ranked by **Impact ÷ Effort** ratio.

### Action 1: Replace the GitHub Issues Contact Flow (2 hours, HIGH IMPACT)
**Impact:** Immediately removes the "not a real company" signal and creates a professional B2B contact experience.  
**What to do:**
- Create `hello@ongarde.io` email (if not done) — Cloudflare Email Routing to CEO personal for now
- Set up a basic Discord server for OnGarde community
- Update GitHub README: clear routing table (bugs → Issues, questions → Discord, business → email)
- Update ongarde.io contact section with same routing
- Set up himalaya skill to handle `hello@ongarde.io` with auto-acknowledgement

**CEO provides:** Email address confirmation, Discord server creation (takes 5 min), approval of routing text  
**Agent handles:** Himalaya configuration, auto-ack template, README draft

---

### Action 2: Polish the GitHub README (3 hours, HIGH IMPACT)
**Impact:** GitHub README is the #1 sales page for developer tools. First impressions matter.  
**What to do:**
- Add animated GIF or screenshot showing interception in action
- Add "Works with" badges: OpenClaw, Agent Zero, CrewAI, LangChain, LangGraph
- Add one-line install command prominently
- Add benchmark badge: `<50ms overhead`
- Add proper license badge, status badge
- Add the contact routing table (see Action 1)
- Ensure fencing brand voice is present ("parry", "en garde", "riposte") but not overdone

**CEO provides:** Approval of final README, the demo GIF/screenshot  
**Agent handles:** Draft of full README, badge generation, formatting

---

### Action 3: Create Twitter/X Account + First 5 Tweets (4 hours, HIGH IMPACT)
**Impact:** Establishes real-time brand presence in the exact community where the target audience is active.  
**What to do:**
- Register `@OnGardeAI` (or check availability: `@OnGardeSec`, `@OnGardeSecurity`)
- Set up profile: bio, logo, header image (crossed épées)
- Post the launch tweet thread (5 tweets)
- Follow 50 target accounts: OpenClaw maintainers, AI agent builders, LLM security researchers
- Pin the demo tweet

**CEO provides:** Twitter account ownership, profile photo approval, writes the launch tweets (authenticity critical)  
**Agent handles:** Draft of follow-list, monitoring setup once account is live

---

### Action 4: Set Up OpenClaw Marketing Agent Monitoring (2 hours, MEDIUM IMPACT)
**Impact:** Creates the intelligence layer that makes everything else more effective. Agent watches so CEO doesn't have to.  
**What to do:**
- Configure Discord skill to monitor OpenClaw community channels for keywords
- Set up RSS monitoring for HN and Reddit keyword searches
- Set up GitHub search monitoring for brand mentions
- Configure daily digest delivery to CEO (via Discord DM or email)

**CEO provides:** Discord server access, preference for digest delivery channel  
**Agent handles:** All configuration, monitoring logic, digest template

---

### Action 5: Write + Publish the Cornerstone Blog Post (6 hours, HIGH IMPACT, LONG-TERM)
**Impact:** Single piece of content that drives SEO, establishes credibility, and can be repurposed across all channels for months.  
**What to post:** "The Security Gap in Every Open-Source AI Agent Platform (And What To Do About It)"  
**Includes:** The market research data, the gap analysis table, honest comparison, OnGarde as the solution (not the headline — the conclusion)

**CEO provides:** Writes the post (cannot be delegated — needs founder voice and authority)  
**Agent handles:** SEO optimization review, cross-posting to DEV.to/Hashnode, social media amplification

---

### CEO vs. Agent Responsibility Matrix

| Task | CEO | Agent |
|------|-----|-------|
| Write all Twitter/X content | ✅ | Draft only |
| Write all Reddit posts | ✅ | Never |
| Write all HN submissions | ✅ | Never |
| Write cornerstone blog posts | ✅ | Draft outlines |
| Write LinkedIn posts | Review & edit | Draft |
| All sales conversations | ✅ | Never |
| Monitor all channels | — | ✅ |
| Tier-1 Discord/GitHub/email responses | Escalations only | ✅ |
| Cross-post approved content | — | ✅ |
| Track metrics | — | ✅ |
| Weekly digest | — | ✅ |
| CRM data entry from leads | — | ✅ |
| GitHub README draft | Review & approve | ✅ |
| Release notes draft | Review & edit | ✅ |

---

## 6. Skills Gap / Tooling Needs

### OpenClaw Skills Required

| Skill | Status | Priority | Purpose |
|-------|--------|----------|---------|
| `discord` skill | ✅ Ready | CRITICAL | Monitor + respond in Discord |
| `himalaya` (email) | Needs setup | CRITICAL | Handle hello@ongarde.io |
| `gh-issues` skill | Needs setup | HIGH | Monitor GitHub, triage issues |
| `blogwatcher` skill | Needs setup | HIGH | Monitor Reddit RSS, HN RSS |
| `web_search` (built-in) | ✅ Available | MEDIUM | Periodic brand mention search |
| `browser` (built-in) | ✅ Available | MEDIUM | Screenshot/verification tasks |
| Slack skill | Low priority | LOW | Internal CEO notifications |

### External Accounts / Tools to Create

| Tool | Purpose | Cost | Priority |
|------|---------|------|----------|
| `hello@ongarde.io` email | Primary inbound contact | Free (Cloudflare routing) | CRITICAL |
| Discord Server (OnGarde Community) | Community support hub | Free | CRITICAL |
| Twitter/X `@OnGardeAI` | Social presence | Free | HIGH |
| Calendly (free tier) | Enterprise/Team demo booking | Free | HIGH |
| LinkedIn Company Page | B2B visibility | Free | HIGH |
| Plausible Analytics (or Vercel Analytics) | Website traffic tracking | $9/mo or free | MEDIUM |
| DEV.to account | Content distribution | Free | MEDIUM |
| Hashnode blog | Content distribution + own domain | Free | MEDIUM |
| Simple CRM (Notion table or Airtable) | Lead tracking | Free | MEDIUM |
| GitHub Topics (add to repo) | Discoverability | Free | HIGH |
| HN account (CEO personal) | Submit + comment | Free | HIGH |

### Skills Configuration Notes

**himalaya (email):**
- Configure IMAP/SMTP for `hello@ongarde.io`
- Set up auto-ack template with doc links and Discord invite
- Configure lead classification: extract company, use case, platform from email body
- Deliver parsed leads to a Notion/Airtable CRM table

**discord skill:**
- Create OnGarde community server first
- Configure monitoring for own server (all channels)
- Configure monitoring for OpenClaw server (read-only, keyword-triggered alerts only)
- Set up daily digest format

**gh-issues skill:**
- Monitor `ongarde` repo for all new issues
- Pattern-match FAQ questions → auto-respond with doc links
- Label new issues: `bug`, `question`, `feature-request`, `integration`
- Flag any security-related issues for immediate CEO escalation

**blogwatcher skill:**
- RSS feeds for subreddits: `https://www.reddit.com/r/LocalLLaMA/.rss`
- RSS feed for HN search: `https://hnrss.org/newest?q=prompt+injection`
- RSS feed for HN search: `https://hnrss.org/newest?q=agent+security`
- Filter by keyword relevance before alerting

---

## Appendix A: KPIs and Success Metrics

### Month 1 Targets
- 500 GitHub stars
- 100 Discord members
- 50 free sign-ups
- 5 qualified leads (Reyes persona or above)
- 1 cornerstone blog post published
- Social accounts established and active

### Month 2 Targets
- 1,000 GitHub stars
- 300 Discord members
- 200 free sign-ups
- 2 paying customers (any tier)
- Integration guides for 3 platforms live
- HN front page hit (at least once)

### Month 3 Targets
- 2,000 GitHub stars
- 500 Discord members
- 5 paying customers
- First Team-tier deal signed
- SEO: ranking for 3+ target keywords

### Conversion Funnel Targets
- Visitor → Free signup: 3–5%
- Free signup → Active user (7-day): 40%
- Active user → Paid conversion: 5% (industry standard for dev tools)
- Paid → Team upgrade: 10% within 6 months

---

## Appendix B: Brand Voice Quick Reference

When drafting any content, apply this filter:

**Use these words:** parry, en garde, riposte, precision, runtime, intercept, proxy, block, detect, audit, zero-code, one line  
**Avoid these words:** industry-leading, cutting-edge, revolutionary, best-in-class, powerful, robust, robust security  

**Tone check:** Would a senior security engineer respect this? Is it technically accurate? Does it make a specific claim we can back up?

**Fencing metaphor use:** Subtle, purposeful, not in every sentence. The brand should feel sophisticated, not like a gimmick. Use the language when it naturally fits, don't force it.

---

## Appendix C: Competitive Monitoring Targets

Monitor these regularly for competitive intelligence and positioning updates:

- **Prisma AIRS** (Palo Alto) — pricing changes, feature announcements
- **Cisco AI Defense** — enterprise market signaling
- **AgentGuardrail** (open source) — community adoption, feature parity
- **LangSmith** (LangChain) — they're moving toward observability + safety
- **Guardrails AI** — open source guardrails library
- New entrants: search GitHub monthly for `ai-agent-security`, `llm-proxy`, `prompt-injection-prevention`

---

*Document produced by OnGarde autonomous marketing strategist agent.*  
*For implementation questions: hello@ongarde.io*  
*Version 1.0 — Ready for CEO review and execution.*
