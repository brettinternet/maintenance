# Maintenance orchestrator

Runs one low-risk Copilot maintenance task per trusted GitHub repository each Monday. Checks the result, then opens a `maintenance/*` PR.

## Setup

1. Create a fine-grained personal access token limited to the target
   repositories. Grant it `Contents: Read and write`,
   `Pull requests: Read and write`, and `Metadata: Read` access. Organization
   repositories may require administrator approval.

2. Add the token to this repository:

   ```bash
   gh secret set MAINTENANCE_GITHUB_TOKEN
   ```

   Track its expiration: publishing stops when the token expires or its owner
   loses repository access. Do not use a broadly scoped classic `repo` token.

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
     instructions: null
     checks:
       - [bun, test]
     draft_pr: true
     runs_on: ubuntu-latest
   repositories:
     - owner: acme
       name: service
       instructions: |
         Prefer type-safety and test-coverage improvements.
         Do not change public API behavior.
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
- `instructions` optionally narrows the maintenance task for a repository. It is appended to the fixed safety prompt, limited to 8,000 characters, and should not duplicate guidance in the target's `AGENTS.md`.
- `checks` are argv arrays shown to Copilot before it works, then executed from the target repository root afterward.
- `runs_on` selects the GitHub-hosted runner label and defaults to `ubuntu-latest`.
- The workflow installs each target's committed mise toolchain before running the agent.
- Every enabled repository needs at least one check; configure its actual test, lint, or build gates rather than only a whitespace check.
- `enabled: false` excludes a repository.
- The dispatch filter must be exactly `owner/name`.

## Boundaries

Copilot cannot access `MAINTENANCE_GITHUB_TOKEN` or publish changes. It receives
only the short-lived built-in workflow token for Copilot requests. The
fine-grained PAT is exposed only to target checkout, open-PR detection, and the
finalizer. The finalizer owns publishing and rejects:

- failed checks
- changed commits or branches
- changes under `.github/workflows/` or `.github/actions/`
- any `.gitmodules` change
- empty changes

An existing open `maintenance/*` PR skips that repository. Jobs run one at a time.

Only configure trusted repositories. Their instructions run inside the agent session.

Commits use the configured author name and email, but authentication and push/PR
activity are attributed to the PAT owner. To receive profile contribution
credit, configure an email linked to that GitHub account and merge the commit
into the repository's default branch.
