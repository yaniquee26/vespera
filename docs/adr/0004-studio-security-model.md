# ADR-0004: Security model of the local web app

**Status:** Accepted
**Date:** 2026-11-25
**Deciders:** maintainers

## Context

Vespera Studio (ADR-0003) runs a web server on the user's computer. It can start runs that call
paid APIs, write a `vespera.yaml`, read scenario files and show model replies. Being local does
not make it safe: any website open in the same browser can send requests to `localhost`.

Threats considered:
1. **Cross-site request forgery:** a page on another site makes the browser POST to Studio and
   starts a run or writes a file.
2. **DNS rebinding:** a hostile domain resolves to `127.0.0.1` and reads Studio's pages as if it
   were the same origin.
3. **Network exposure:** the server listens on all interfaces and becomes reachable from the LAN.
4. **Hostile content:** model replies are attacker-controlled text and may contain HTML or script.
5. **Secret leakage:** an API key shown on a page, written to a file or echoed in an error.
6. **Path traversal:** a form field pointing outside the project folder.

## Decision

- Listen on `127.0.0.1` only; any other host is refused at start-up.
- Allow only `Host: 127.0.0.1` and `Host: localhost` (trusted-host middleware), which defeats
  DNS rebinding.
- Every state-changing action is a POST that must carry a per-process random token in a request
  header, compared in constant time. When present, the `Origin` header must be Studio's own, and
  `Sec-Fetch-Site` must be `same-origin` or `none`.
- API keys never enter Studio. Forms take only the **name** of an environment variable; the name
  must match `^[A-Z_][A-Z0-9_]{0,63}$`, and input that looks like a key is refused without being
  echoed back. Pages only show whether a variable is set.
- File paths are resolved and must stay inside the project folder, with an allowed extension.
- Every page is autoescaped. A strict Content Security Policy allows scripts and styles only from
  Studio itself, with no inline code; interval bars are drawn as SVG instead of inline styles.
  `X-Content-Type-Options: nosniff`, `Cache-Control: no-store`, `frame-ancestors 'none'`.
- Downloads (`results.json`, JUnit) are masked by default, as in the CLI.

## Options considered

### Option A: No protection, because the server is local
**Cons:** open to threats 1–3; unacceptable for a security tool.

### Option B: A secret token in the URL (as notebook servers do)
**Pros:** blocks cross-site requests and rebinding.
**Cons:** the token ends up in browser history and screenshots; links cannot be shared on the
same machine without it.

### Option C: Host allowlist + header token + Origin and Fetch-Metadata checks (chosen)
**Pros:** blocks threats 1–3 without a token in the URL. Each layer is simple and testable on its
own.
**Cons:** several small mechanisms to maintain; the vendored htmx must send the header token on
every request.

## Trade-off analysis

A tool that tests the security of chatbots must not itself be the weak point on the tester's
machine. Option C costs a little code and gives defence in depth: if one check is bypassed, the
others still stand. Threats 4–6 are handled the same way in every option.

## Consequences

- Every rule above has an automated test: a foreign `Host`, a missing or wrong token, a foreign
  `Origin`, a path outside the project, a key-like variable name. A sentinel environment value
  must never appear in any response, and every response must carry the CSP.
- Browser tests fail on any console error, which catches CSP violations.
- `SECURITY.md` summarises this model for users and explains how to report a problem.

## Action items

1. [ ] Security middleware and its tests (S3).
2. [ ] `SECURITY.md` (S9).
