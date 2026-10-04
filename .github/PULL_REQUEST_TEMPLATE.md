## Summary

<!-- What does this PR change, and why? Link the issue it closes (Fixes #...). -->

## Type of change

- [ ] Bug fix
- [ ] New feature
- [ ] Refactor / internal cleanup
- [ ] Documentation
- [ ] CI / build / packaging

## Checklist

- [ ] `ruff check .` passes
- [ ] `pytest -q tests` passes (SQLite)
- [ ] The Postgres leg passes (`PVE2_DB_URL=postgresql://pve2:pve2pass@127.0.0.1:5432/pve2 pytest -q tests`)
- [ ] New/changed SQL is portable across SQLite and Postgres
- [ ] No secrets, tokens or private hostnames committed
- [ ] Docs/CHANGELOG updated when behaviour is user-visible

## Notes for reviewers

<!-- Anything worth calling out: trade-offs, follow-ups, screenshots. -->
