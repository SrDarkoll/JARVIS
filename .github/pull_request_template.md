## Summary

<!-- Explain what changed and why. Keep the scope focused. -->

## Related Issue

<!-- Use "Closes #123" when applicable. Write "None" if there is no issue. -->

## Change Type

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor
- [ ] Documentation
- [ ] Security or dependency update
- [ ] Build, CI, or installation change

## Validation

<!-- List the commands and manual flows you ran, including their results. -->

```text
pytest -q
python -m ruff check src/backend
python -m pyright src/backend
python -m compileall -q start_app.py src/backend
```

## Platform Testing

- [ ] Windows
- [ ] macOS
- [ ] Linux
- [ ] Not platform-specific

<!-- Describe any hardware, browser, microphone, Spotify, or WebView2 testing. -->

## Checklist

- [ ] The change is focused and does not include unrelated generated files.
- [ ] Existing behavior remains covered, or tests were added for the change.
- [ ] Applicable tests and static checks pass locally.
- [ ] User-facing behavior, configuration, and environment variables are documented.
- [ ] No `.env`, credentials, OAuth caches, logs, recordings, profiles, or runtime databases are included.
- [ ] Error messages and logs do not expose secrets or private system details.
- [ ] Security-sensitive actions still require the expected policy and confirmation checks.
- [ ] Frontend JavaScript syntax checks were run when frontend files changed.
- [ ] Git LFS was used for any required large model assets.

## Screenshots Or Logs

<!-- Add sanitized evidence when useful. Remove secrets and personal data. -->

## Reviewer Notes

<!-- Call out risks, migrations, known limitations, or follow-up work. -->
