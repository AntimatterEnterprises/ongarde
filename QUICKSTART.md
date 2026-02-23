# OnGarde.io Quick Start Guide

**Last Updated:** February 17, 2026  
**Status:** 📚 Documentation Complete → 🚀 Ready for Implementation

---

## 🎯 What We're Building

**OnGarde.io** - A zero-trust security proxy for agentic AI frameworks
- **Tagline:** "Firewall for Autonomy"
- **Performance Target:** < 50ms overhead
- **Core Function:** Intercept → Audit → Block/Allow → Stream

---

## 📁 Current Project Status

### ✅ Completed
- [x] Directory structure created
- [x] Core documentation written
- [x] Cursor AI rules established
- [x] Infrastructure architecture defined
- [x] Dependencies specified
- [x] AI integration roadmap planned
- [x] Development workflow documented

### 🚧 Sprint 1 (Current) - The Interceptor
**Goal:** FastAPI proxy with streaming support

**Files to implement:**
1. `app/utils/logger.py` - Structured logging
2. `app/proxy/streaming.py` - Stream handling
3. `app/proxy/engine.py` - Proxy logic
4. `app/main.py` - FastAPI app

---

## 🏗️ Tech Stack

| Component | Technology | Why |
|-----------|-----------|-----|
| Proxy | Python 3.12 + FastAPI | Async-first, high performance |
| Host | Railway | Auto-scaling, GitHub integration |
| Database | Supabase | PostgreSQL + RLS + Auth |
| Frontend | Vercel | Fast static hosting |
| CDN/WAF | Cloudflare | DDoS protection, SSL |

---

## 📚 Key Documentation Files

### Must Read Before Coding
1. **docs/BRD.md** - Complete business requirements
2. **.cursor/rules/000-core-project.mdc** - Core coding standards
3. **README.md** - Project overview and setup
4. **CHANGELOG.md** - Current sprint status and progress

### Reference During Coding
- **.cursor/rules/100-security-engine.mdc** - Security implementation patterns
- **.cursor/rules/200-supabase-db.mdc** - Database schema and queries
- **CHANGELOG.md** - Track all changes here

---

## 🔧 Development Workflow

### Before Starting Any Feature
1. ✅ Read sprint goals from CHANGELOG.md
2. ✅ Review relevant .cursor rules
3. ✅ Update documentation if needed
4. ✅ Understand acceptance criteria

### During Implementation
1. 💻 Follow async-first patterns
2. 🔐 Never log PII or credentials
3. ⚡ Keep performance < 50ms in mind
4. 📝 Add docstrings to all functions
5. 🧪 Write tests alongside code

### After Implementation
1. ✅ Run tests: `pytest tests/ -v`
2. ✅ Update CHANGELOG.md
3. ✅ Update relevant documentation
4. ✅ Commit with clear message

---

## 🎨 Code Style

### Type Safety
```python
from pydantic import BaseModel

class LLMRequest(BaseModel):
    messages: List[Message]
    stream: bool = False
```

### Async Patterns
```python
async def scan_request(request: LLMRequest) -> ScanResult:
    """All I/O operations MUST be async"""
    result = await analyze_content(request.messages)
    return result
```

### Error Handling
```python
try:
    result = await risky_operation()
except Exception as e:
    logger.error("Operation failed", error=str(e), request_id=req_id)
    # Fail-safe: Block on error
    return ScanResult(allowed=False, reason="scan_error")
```

---

## 🔒 Security Principles

### The Golden Rules
1. **Zero Trust** - Every request is potentially malicious
2. **Fail-Safe** - Block by default on errors
3. **No Logging PII** - Redact before storing
4. **Performance** - < 50ms overhead always
5. **Async Everything** - Never block the event loop

### Blocked by Default
- Shell commands: `sudo`, `rm -rf`, `dd`, fork bombs
- File access: `.env`, `.ssh/*`, `credentials.*`
- Network: reverse shells, eval pipes

---

## 📊 Testing Standards

### Coverage Requirements
- Security functions: 100% coverage
- Proxy logic: > 90% coverage
- Database operations: > 80% coverage
- Overall project: > 80% coverage

### Test Types
```python
# Unit tests
async def test_command_scanner():
    result = await scan_text_content("sudo rm -rf /")
    assert result.allowed == False

# Performance tests
async def test_scan_performance():
    start = time.time()
    await scan_request(mock_request)
    assert (time.time() - start) < 0.05  # 50ms
```

---

## 🚀 Quick Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run in development
uvicorn app.main:app --reload --port 8000

# Run tests
pytest tests/ -v

# Run tests with coverage
pytest tests/ --cov=app --cov-report=html

# Format code
black app/ tests/

# Lint code
ruff check app/ tests/

# Type check
mypy app/
```

---

## 🎯 Sprint 1 Checklist

- [ ] **app/utils/logger.py**
  - [ ] Structured logging with structlog
  - [ ] Request ID tracking
  - [ ] Performance metrics
  - [ ] JSON output format

- [ ] **app/proxy/streaming.py**
  - [ ] OpenAI SSE parser
  - [ ] Anthropic stream handler
  - [ ] Async generator wrappers
  - [ ] Error handling

- [ ] **app/proxy/engine.py**
  - [ ] HTTPX async client
  - [ ] Request forwarding
  - [ ] Header management
  - [ ] Response streaming

- [ ] **app/main.py**
  - [ ] FastAPI app initialization
  - [ ] `/health` endpoint
  - [ ] `/v1/chat/completions` (OpenAI)
  - [ ] `/v1/messages` (Anthropic)
  - [ ] Error middleware

---

## 🆘 Getting Help

- **Documentation Issues:** Check docs/BRD.md
- **Coding Standards:** Check .cursor/rules/
- **Architecture Questions:** Check README.md or docs/BRD.md
- **Security Patterns:** Check .cursor/rules/100-security-engine.mdc

---

## 📈 Success Metrics

### MVP Goals
- ✅ < 50ms latency overhead
- ✅ 100% block rate on dangerous commands
- ✅ Zero false positives on legitimate commands
- ✅ 99.9% uptime
- ✅ Maintain native LLM streaming performance

### Ready to Code?
Start with Sprint 1, file by file:
1. `app/utils/logger.py`
2. `app/proxy/streaming.py`
3. `app/proxy/engine.py`
4. `app/main.py`

**Let's build! 🚀**
