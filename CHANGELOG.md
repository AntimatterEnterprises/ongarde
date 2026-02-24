# Changelog

All notable changes to OnGarde.io will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [1.0.0-beta.2] - 2026-02-24

### Security

- **Auth required by default.** `ONGARDE_AUTH_REQUIRED` now defaults to `true`. API keys are
  required out of the box. The `npx @ongarde/openclaw init` wizard creates the first key
  automatically. Previously the default was `false` (no authentication).

- **Dashboard is localhost-only.** `/dashboard` now enforces loopback-only access at the code
  level. Requests originating from non-loopback IP addresses receive HTTP 403.

- **Swagger UI disabled in production.** `/docs` and `/redoc` are no longer served when
  `DEBUG=false` (the default). Set `DEBUG=true` to re-enable for local development.

- **`audit_path` removed from `/health` response.** The health endpoint no longer exposes
  filesystem paths in its JSON output.

- **Upstream URL validation.** OnGarde now rejects `http://` connections to raw private and
  cloud-metadata IP ranges (169.254.x.x, 10.x.x.x, 172.16–31.x.x, 192.168.x.x) at startup,
  preventing SSRF via config. `http://localhost:*` remains allowed (e.g. for local Ollama).

- **Rate limiting on key management endpoints.** `/dashboard/api/keys/*` is now rate-limited
  to 20 requests per minute per IP.

### Added

- **CI security scanning pipeline.** New GitHub Actions workflow runs `bandit` (Python static
  analysis) and `pip-audit` (dependency vulnerability scan) on every push and pull request.

- **`slowapi` dependency.** Added to `requirements.txt` and `pyproject.toml` to power
  rate limiting on key management endpoints.

---

## [Unreleased]

### Marketing Website Complete - 2026-02-17

#### Launched
- ✅ **Website LIVE at ongarde.io** (deployed on Vercel)
- ✅ Custom domain connected and configured
- ✅ Professional branding with crossed swords logo
- ✅ Multi-platform positioning (OpenClaw, Agent Zero, CrewAI, LangChain, etc.)
- ✅ Fully mobile responsive design
- ✅ Open-source Lucide icon integration
- ✅ Engaging CSS animations and interactions
- ✅ Professional polish and UX improvements

#### Market Research
- Analyzed 15 agent orchestration platforms
- Identified 50K-100K developer TAM
- Confirmed security gap: No open-source platforms offer runtime content security
- Strategic positioning: Universal security for self-hosted platforms

#### Brand Strategy
- Fencing theme ("En Garde" metaphor)
- Crossed swords/épées visual identity
- Purple gradient color palette
- Professional but approachable for open-source community

### Sprint 1: The Interceptor - 2026-02-17 (✅ COMPLETED)

#### Added
- `app/utils/logger.py` - Structured async logging with structlog
  - Request ID tracking with context variables
  - Performance logging with 50ms threshold warnings
  - JSON output for production, pretty console for development
  - PerformanceLogger context manager for operation timing
- `app/proxy/streaming.py` - SSE parsing and streaming utilities
  - OpenAI and Anthropic streaming response handlers
  - StreamChunk class for uniform chunk handling
  - Stream buffering and inspection utilities for security scanning
  - Maintains native streaming performance
- `app/proxy/engine.py` - Main proxy forwarding logic
  - HTTPX async client with connection pooling
  - Provider detection from request paths
  - Header forwarding and authentication pass-through
  - Request ID generation and tracing
  - Comprehensive error handling with proper HTTP status codes
- `app/main.py` - FastAPI application entry point
  - Health check endpoint (`/health`)
  - OpenAI compatibility endpoint (`/v1/chat/completions`)
  - Anthropic compatibility endpoint (`/v1/messages`)
  - CORS middleware configuration
  - Global exception handlers with structured logging
  - Environment-based configuration

#### Technical Details
- All code is async-first (no blocking operations)
- Performance monitoring built into every operation
- Request tracing with unique IDs
- Streaming support for both OpenAI and Anthropic APIs
- Proper cleanup with lifespan management

#### Testing Status
- ✅ No linting errors
- ⏳ Unit tests pending (Sprint 4)
- ⏳ Integration tests pending (Sprint 4)

### Documentation Phase - 2026-02-17

#### Added
- Initial project structure and directory scaffolding
- Comprehensive Business Requirements Document (BRD.md)
- Complete Cursor AI rules for coding standards:
  - 000-core-project.mdc - Core project guidelines
  - 100-security-engine.mdc - Security implementation patterns
  - 200-supabase-db.mdc - Database schema and integration
- Complete README.md with setup instructions
- QUICKSTART.md developer quick reference guide
- CHANGELOG.md for sprint tracking and progress
- Full dependency list in requirements.txt
- Infrastructure stack documentation
- AI integration roadmap for future implementation
- Mandatory documentation update policy

#### Infrastructure Defined
- Proxy Logic: Python/FastAPI
- Backend Host: Railway
- Data & Auth: Supabase
- Frontend: Vercel
- Network/WAF: Cloudflare

#### Planned Features
- FastAPI proxy with streaming support (Sprint 1)
- Security scanner "The Parry" (Sprint 2)
- Audit vault with Supabase (Sprint 3)
- Comprehensive testing suite (Sprint 4)
- CLI security scanning tool

---

## [0.0.0] - 2026-02-17

### Added
- Project initialization
- Git repository setup
- License (MIT)
- .gitignore and .gitattributes

---

## Sprint Goals

### Sprint 1: The Interceptor (✅ COMPLETED)
**Goal:** FastAPI proxy with 100% OpenAI/Anthropic streaming parity
- [x] Logger implementation
- [x] Streaming utilities
- [x] Proxy engine
- [x] FastAPI application
- [x] Health check endpoint

### Strategic Pivot: OpenClaw Integration (In Progress)
**Goal:** Build "install and forget" security for OpenClaw users

**Decision Rationale:**
- OpenClaw is gaining rapid adoption but lacks built-in security
- Multiple token/routing complexity creates credential leak risks
- VM → VPS gateway architecture needs transparent protection
- Skills marketplace needs security layer to be enterprise-ready
- Opportunity to be THE security solution for OpenClaw ecosystem

**Approach:**
- Build Node.js package for OpenClaw integration
- Create automatic configuration helper
- Deploy lightweight proxy (reuse Sprint 1 code)
- Zero manual configuration for users
- Terminal access to live OpenClaw needed for proper implementation

**Status:** Day 1 Complete - Diagnostic tool ready, website in progress

**Session Checkpoint Created:** `SESSION_CHECKPOINT.md`
- Complete state summary
- Build plan documented
- Resume instructions for tonight
- Open questions tracked

**Marketing Website Created & Deployment Ready:**
- `website/index.html` - Complete landing page
- `website/css/style.css` - Full styling
- `website/js/main.js` - Interactive features
- `website/vercel.json` - Vercel configuration
- Hero, features, pricing, installation guide
- Mobile responsive
- **Vercel chosen as hosting platform**
- Ready for immediate deployment

**Next Steps:**
1. Run OpenClaw diagnostic tonight
2. Analyze results and finalize integration approach
3. Build Node.js CLI tool with exact parameters
4. Deploy website to production

**Integration Method Confirmed:**
- Uses OpenClaw's native `models.providers.baseUrl` configuration
- No plugin development needed
- Clean, documented, officially supported approach
- OnGarde proxy + CLI configuration manager

**Tools Created:**
- `tools/openclaw-diagnostic.sh` - Comprehensive diagnostic script
- Gathers all information needed for exact integration
- Sanitizes sensitive data automatically
- Ready to run on OpenClaw server

### Sprint 2: The Parry (Pending - Will Integrate into OpenClaw Package)
**Goal:** Block dangerous commands before execution
- [ ] Security rule definitions
- [ ] Scanner implementation
- [ ] Integration with proxy
- [ ] Performance optimization

### Sprint 3: The Audit Vault (Planned)
**Goal:** Persistent logging of security events
- [ ] Supabase client
- [ ] Database schema migration
- [ ] Audit logging functions
- [ ] Background task system

### Sprint 4: Testing & Hardening (Planned)
**Goal:** Ensure reliability and security
- [ ] Unit tests
- [ ] Integration tests
- [ ] Performance benchmarks
- [ ] Security hardening
