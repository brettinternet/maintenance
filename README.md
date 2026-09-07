# Maintenance orchestrator

Runs one low-risk Copilot maintenance task per trusted GitHub repository each Monday. Checks the result, then opens a `maintenance/*` PR.

## Setup

1. Create a GitHub App with access to each target repository:

   | Permission | Access |
   | --- | --- |
   | Contents | Read and write |
   | Pull requests | Read and write |
   | Metadata | Read |

2. Add the App credentials to this repository:

   ```bash
   gh secret set MAINTENANCE_APP_ID
   gh secret set MAINTENANCE_APP_PRIVATE_KEY < path/to/private-key.pem
   ```

3. Enable Copilot for the account or organization running the workflow.

4. Copy a target from `config.example.yaml` into `config.yaml`:

   ```yaml
   version: 1
   defaults:
     enabled: true
     model: auto
     commit_author:
       name: Maintenance Bot
       email: maintenance-bot@users.noreply.github.com
     base_branch: null
     checks:
       - [bun, test]
     draft_pr: true
   repositories:
     - owner: acme
       name: service
       checks:
         - [bun, test]
         - [python, -m, pytest, -q]
   ```

Keep credentials in GitHub Actions secrets, not `config.yaml`.

## Validate

```bash
mise install
mise exec uv -- uv sync --frozen
./scripts/validate-config.sh
mise exec uv -- uv run pytest
mise exec shellcheck -- shellcheck scripts/*.sh
mise exec actionlint -- actionlint -config-file .github/actionlint.yaml .github/workflows/maintenance.yml
```

## Preview one repository

```bash
gh workflow run maintenance.yml \
  --field repository=acme/service \
  --field dry_run=true
```

A dry run executes Copilot and the configured checks without creating a branch, commit, push, or PR.

## Configuration

- Repository fields override `defaults`.
- `base_branch: null` uses the repository's default branch.
- `checks` are argv arrays executed from the target repository root.
- Every enabled repository needs at least one check.
- `enabled: false` excludes a repository.
- The dispatch filter must be exactly `owner/name`.

## Boundaries

Copilot cannot access the GitHub App token or publish changes. The finalizer owns publishing and rejects:

- failed checks
- changed commits or branches
- changes under `.github/workflows/` or `.github/actions/`
- any `.gitmodules` change
- empty changes

An existing open `maintenance/*` PR skips that repository. Jobs run one at a time.

Only configure trusted repositories. Their instructions run inside the agent session.
