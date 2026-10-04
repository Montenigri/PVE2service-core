# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in PVE2Services, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Report it privately through GitHub: open the repository's **Security** tab →
**Advisories** → **Report a vulnerability** (GitHub Private Vulnerability
Reporting). The report stays visible only to the maintainers until a fix is
released.

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

## Response Timeline

- **Acknowledgment:** Within 48 hours
- **Initial assessment:** Within 1 week
- **Fix or mitigation:** Depends on severity, typically within 2 weeks

## Security Measures

PVE2Services implements the following security measures:

- **Authentication:** HMAC-SHA256 session tokens with bcrypt password hashing
- **CSRF Protection:** Token-based CSRF validation on all state-changing requests
- **Rate Limiting:** Login attempts rate-limited (5 attempts/60s, 300s block)
- **Security Headers:** X-Content-Type-Options, X-Frame-Options, X-XSS-Protection, HSTS
- **Template Sandbox:** Jinja2 sandboxed environment for user-generated content
- **SSH Security:** StrictHostKeyChecking=accept-new, no password auth
- **SQL Injection Prevention:** Parameterized queries via SQLAlchemy
- **SSRF Protection:** Blocked hostnames/IPs for Proxmox URL validation
- **Secret Encryption:** Optional Fernet encryption for stored credentials

## Supported Versions

| Version | Supported |
|---------|-----------|
| 0.1.x | Yes |

## Disclosure Policy

We follow coordinated disclosure. Please give us reasonable time to address the issue before public disclosure.
