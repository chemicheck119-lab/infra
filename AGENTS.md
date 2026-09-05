# AGENTS.md

## Scope

These instructions apply to the entire infrastructure repository.

## Safety

- Never commit credentials, secrets, raw datasets, model weights, or state files.
- Keep ML jobs manual, single-task, retry-bounded, and time-bounded unless a reviewed change explicitly says otherwise.
- Do not describe preview deployments as production operations.
- Prefer least-privilege service accounts and private storage.

## Validation

- Run `bash -n scripts/*.sh`.
- Run `scripts/test_config.sh`.
