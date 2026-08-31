# Security Policy

## Reporting a vulnerability

If you've found a security vulnerability in Singularity AI, **do not open a public issue**.
Instead:

- Use GitHub's **[Report a vulnerability](https://github.com/Victor-Alves0/AI-Workspace/security/advisories/new)**
  (the **Security → Advisories** tab) for a private report, **or**
- Contact the repository maintainer privately.

Include, if possible:

- A description of the issue and its impact.
- Steps to reproduce (or a proof of concept).
- The affected version/commit and the environment.

You'll get a response once the report is triaged. Please allow a reasonable time for a fix before
any public disclosure.

## Scope

This is a **self-hosted** project: whoever installs it is responsible for protecting their own
infrastructure (network, backups, `APP_SECRET`, the Postgres password, HTTPS). Hardening
recommendations are in [docs/security.md](docs/security.md).

Points especially worth keeping in mind:

- **`ALLOW_CODE_MODE`** executes model-generated code in a sandbox — read the caveats in
  [docs/security.md](docs/security.md#tool-execution-sandbox) before exposing it to untrusted
  users.
- **Secrets** depend on `APP_SECRET`; treat it as cryptographic material.
</content>
