# Public-release security review — 2026-10-22

Scope: the SDK-ingestion, durable-storage, MCP and self-hosting changes in this
branch, plus dependency advisory checks. This is a source/publication review,
not a penetration test, an independent audit, or a claim of no vulnerabilities.
It does not deploy updated dependencies to existing running containers.

## Publication boundary

- Local credentials, environment files, event databases, backups, the private
  deployment compose and operator runbook are excluded from Git and Docker
  build contexts. The public compose example binds only to loopback.
- Gitleaks 8.30.1 scans the public tree and outgoing commits with redaction.
  Allowances are restricted to the RFC WebSocket example nonce, one mocked
  frontend password, and two lines that reference randomly generated test
  passwords. Test directories are not broadly excluded.
- Staged content is also compared locally with actual deployment secret values
  without publishing those values or their hashes.

## Dependency findings

- Rust lockfile updates: `rustls` 0.23.46, `rustls-webpki` 0.103.16,
  `webpki-roots` 1.0.10, `anyhow` 1.0.104 and `event-listener` 5.4.2 address
  the applicable advisory findings. Cargo builds use the lockfile.
- One lockfile advisory remains:
  [RUSTSEC-2023-0071](https://rustsec.org/advisories/RUSTSEC-2023-0071/) for
  `rsa` 0.9.10. It belongs to optional SQLx MySQL dependencies and is not in
  this workspace's enabled dependency graph, including all target platforms.
  There is no patched version. Do not silently treat a future build that
  enables this dependency as safe.
- `cargo audit` 0.22.2 reports no other findings after that single explicitly
  scoped exception. No repository-wide advisory suppression is installed.
- Frontend dependencies and the lockfile were updated for reported advisories.
  Vite moves from 5 to patched 6 with the matching Svelte plugin. A narrow
  `cookie` override uses 0.7.2 or later because SvelteKit still requests the
  affected 0.6 line; see
  [GHSA-pxg6-pf52-xh8x](https://github.com/advisories/GHSA-pxg6-pf52-xh8x).
  The final Bun audit reports no known vulnerabilities.
- `pip-audit` reports no known vulnerabilities for the MCP and integration-test
  requirements resolved during this review. Only the direct MCP dependency is
  pinned; repeat the audit when installing or updating transitive dependencies.

Reproduce the Rust exception check (requires a current advisory database):

```sh
cargo tree --locked --target all -i rsa
# Expected: no dependency path. If a path appears, reassess before proceeding.
cargo audit --ignore RUSTSEC-2023-0071
```

## Security behavior checked

- Event ingestion authenticates project keys, bounds compressed/decompressed
  payloads, rejects nonstandard large-window Brotli, and scrubs recognized
  sensitive structured fields before storing preserved SDK extensions.
- Success waits for durable storage; failure returns 503. Tests exercise
  duplicate retries, storage failure, restart persistence and SIGTERM drain.
- MCP defaults to read-only tools. A viewer cannot change issue status even
  if the mutation tool is explicitly advertised. Credentials are not tool
  arguments; redirects and ambient HTTP proxies are disabled. Remote
  plaintext HTTP requires an explicit unsafe-network opt-in.
- Webhook retry state is transactional and survives restarts. Response bodies
  are not buffered. Existing private-address URL checks and redirect refusal
  are retained, but are not a complete DNS-rebinding defense.

## Validation

- Rust formatting, warning-free Clippy and 474 tests passed in an isolated
  build with the updated lockfile.
- Frontend type checking, 311 tests and static production build passed.
- Nine Python client tests passed, including TLS policy, redirect refusal and
  response-size bounds.
- Seven disposable-daemon integration tests passed with each of Python Sentry
  SDK 2.5.1 and 2.51.0, including viewer authorization and MCP tool exposure.
- Shell syntax, production Compose interpolation and Git whitespace checks
  passed. Production data and credentials were not used in integration tests.
- The release Docker image built with the audited lockfile and tested static
  frontend. Its healthcheck passed as UID/GID 65532 with a read-only root,
  dropped capabilities, no network access and a disposable data directory.

## Remaining operational limits

Event messages, URLs, source snippets and other free text may still contain
secrets or personal data. SDK-side filtering and restricted access remain
necessary. Viewers can read all projects on an instance. Webhook retries are
at-least-once and can accumulate on disk; impose retention/monitoring and
restrict outbound networking. Backups contain secrets, are not encrypted by
the script and need protected off-site storage. See [SELF_HOSTING.md](SELF_HOSTING.md).
