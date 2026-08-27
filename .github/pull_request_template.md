## What

<!-- One or two lines. Link the issue or the plan phase this closes. -->

## Checklist

- [ ] `task lint` and `task test` pass locally
- [ ] Tests cover the changed logic — an API test with ENA faked, a Playwright
      test for page wiring, or both
- [ ] No test reaches ENA, and no credential is written to disk or a log
- [ ] A new writable field is in `_EDITABLE` *and* has a test proving the rest
      of the record survives the MODIFY
- [ ] `README.md` / `IMPLEMENTATION_PLAN.md` still describe what the code does
