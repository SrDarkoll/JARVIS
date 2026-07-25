# Security Policy

## Supported Versions

J.A.R.V.I.S. is currently alpha software. Security fixes are applied to the
latest revision of the default branch.

| Version | Supported |
| --- | --- |
| Latest default branch | Yes |
| Older revisions, forks, and unofficial builds | No |

When tagged releases begin, this table will be updated with an explicit support
window.

## Reporting a Vulnerability

Do not report suspected vulnerabilities in a public issue, discussion, pull
request, or log attachment.

Use GitHub's
[private vulnerability reporting form](https://github.com/SrDarkoll/JARVIS/security/advisories/new).
Include:

- A concise description of the vulnerability and its potential impact.
- The affected commit, branch, component, and operating system.
- Reproduction steps or a minimal proof of concept.
- Any conditions required to trigger the issue.
- Suggested mitigations, if known.

Remove API keys, OAuth tokens, credentials, personal conversation history,
voice recordings, and unrelated private data from every report.

If the private form is unavailable, open a public issue asking the maintainer
to provide a private security contact. Do not disclose vulnerability details in
that issue.

## Response Process

The maintainer will aim to:

- Acknowledge a complete report within 7 days.
- Provide an initial assessment within 14 days.
- Coordinate testing and disclosure with the reporter.
- Publish a fix or mitigation before public technical details when practical.

These are response targets rather than a service-level guarantee. Complex or
hardware-specific issues may require additional time.

## Scope

Reports are especially useful when they involve:

- Authentication, authorization, origin checks, or API token handling.
- Command execution, file writes, path traversal, or unsafe desktop control.
- Exposure of environment variables, OAuth state, logs, or conversation data.
- Injection through chat, tools, plugins, search results, or external APIs.
- Security boundary bypasses in Spotify, Telegram, voice, or browser routes.
- Vulnerable dependency behavior with a reproducible impact on J.A.R.V.I.S.

Reports that require publishing real secrets, accessing another person's
system without permission, or disrupting third-party services are not
acceptable. Test only systems and accounts you own or are explicitly
authorized to assess.

## Safe Harbor

Good-faith research that follows this policy, avoids privacy violations and
service disruption, and gives the project reasonable time to address the issue
will be treated as authorized security research for this project. This policy
does not authorize testing of third-party services or infrastructure.
