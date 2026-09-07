# Maintenance orchestrator

This standalone project runs one deliberately small, low-risk maintenance task
at a time across a configured set of trusted GitHub repositories. The weekly
workflow also supports manual dispatch, a one-repository filter, and dry runs.
`config.yaml` is intentionally public-safe and currently has no active targets.
Copy the shape in `config.example.yaml` when adding repositories.

## Setup

1. Create a GitHub App and install it on every target repository. Give the
   installation `Contents: Read and write`, `Pull requests: Read and write`,
   and `Metadata: Read` permissions. The App must be allowed to create branches
   and pull requests in each configured owner.
2. In the repository that hosts this project, add these Actions secrets:

   ```text
   MAINTENANCE_APP_ID
   MAINTENANCE_APP_PRIVATE_KEY
   ```

   `MAINTENANCE_APP_PRIVATE_KEY` is the PEM private key generated for the App.
   For example, with GitHub CLI:

   ```bash
   gh secret set MAINTENANCE_APP_ID
   gh secret set MAINTENANCE_APP_PRIVATE_KEY < path/to/private-key.pem
   ```

3. Edit `config.yaml`, then validate it. Do not put tokens, private keys, or
   encrypted-secret configuration in this repository; this project does not
   use SOPS or AGE.
4. Enable Copilot for the account or organization that owns this workflow.
   The workflow requests `copilot-requests: write` and passes the built-in
   Actions `GITHUB_TOKEN` to Copilot CLI.

The App must be installed on the actual target repositories, not merely on the
orchestrator repository. Its installation token is used for target checkout,
push, and PR creation. App-created pushes and PRs are used so normal target CI
can trigger; this is different from publishing with `GITHUB_TOKEN`.

## Configuration

The YAML schema is strict: unknown keys, duplicate keys, invalid owner/repository
names, and malformed checks fail before the matrix is emitted. Repository
entries require `owner` and `name`; all other fields override `defaults`.
`commit_author` can override either nested `name` or `email` and inherits the
other value.

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
    enabled: true
    checks:
      - [bun, test]
      - [python, -m, pytest, -q]
```

`checks` is the safe, documented representation: a list of argv arrays. Each
enabled repository must resolve to at least one check. Each array is passed
directly to a subprocess from the target repository root; the runner does not
invoke a shell or concatenate command strings. A shell can be requested
explicitly as an argv such as `[bash, -euo, pipefail, -c, ...]`, but that should
be treated as trusted configuration.

`base_branch: null` uses the checked-out default branch. `draft_pr` defaults to
true. A disabled repository is excluded from the Actions matrix. The
`repository` dispatch input must be exactly `owner/name`.

## Running and checking locally

The pinned tools, including Python, uv, Node, Bun, GitHub CLI, actionlint,
shellcheck, and `@github/copilot`, are managed by `mise.toml`:

```bash
mise install
mise exec uv -- uv sync --frozen
./scripts/validate-config.sh
mise exec uv -- uv run pytest
mise exec shellcheck -- shellcheck scripts/*.sh
mise exec actionlint -- actionlint -config-file .github/actionlint.yaml .github/workflows/maintenance.yml
```

Use `mise install` for Copilot CLI; do not install it with an ad-hoc package
command. The workflow installs the same pinned Copilot package through mise.
The checked-in actionlint configuration has one narrow compatibility ignore for
`copilot-requests`, which actionlint 1.7.12 does not yet recognize; remove that
ignore when the linter adds the GitHub permission.

Dispatch a preview with GitHub CLI after adding a target:

```bash
gh workflow run maintenance.yml --field repository=acme/service --field dry_run=true
```

A dry run lets the agent and configured checks inspect an ephemeral checkout,
but the finalizer creates no branch, commit, push, or PR. A normal run first
skips a repository with an existing open `maintenance/*` PR, then asks Copilot
for at most one task. Jobs have `max-parallel: 1` and a concurrency lock.

## Safety and attribution

The Copilot prompt requires repository instructions to be followed, prohibits
commit/push/PR operations, rejects freshness-only churn, and excludes workflow,
action, authentication, security, release, and publishing changes. The
post-agent finalizer runs configured argv checks, rejects changes under
`.github/workflows/`, `.github/actions/`, or `.gitmodules` (including nested
`.gitmodules`), skips clean trees, and only then creates a unique
`maintenance/*` branch and PR.

The target checkout uses the GitHub App token with
`persist-credentials: false`. The Copilot step receives only the built-in
workflow `GITHUB_TOKEN`; the App token is not in its environment or Git
credentials. The later finalizer alone receives the App token for git push and
`gh pr create`. The finalizer also verifies that Copilot did not change the
checked-out commit or branch. Target repositories are treated as trusted
because repository instructions and agentic prompt injection cannot be made
harmless by this wrapper.

The configured commit author is separate from authentication. For GitHub
attribution, use an author email linked to the intended GitHub account, and the
commit must ultimately reach that repository's default branch. The
authentication/pusher identity remains the GitHub App installation, not the
configured author name.

## Cost and supply-chain notes

Each enabled target can consume an Actions job and Copilot model credits; a
weekly run with many targets also consumes runner minutes and tool-install
bandwidth. Keep the target list, checks, model, and schedule intentional, and
use the repository filter or dry run for testing. Copilot billing and limits
are controlled by the relevant GitHub Copilot plan.

Workflow actions are pinned to full commit SHAs with release comments. The
pinned runtime tools and Copilot package are fetched by mise, so their release
artifacts and the runner itself remain supply-chain dependencies; review and
upgrade pins deliberately. Keep App permissions limited to installed trusted
repositories and rotate the App key through Actions secrets.
