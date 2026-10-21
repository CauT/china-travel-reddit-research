# China Travel Reddit Research Collector

A bounded, PostgreSQL-backed reference implementation for collecting distinct questions about travel in China from an explicitly approved Reddit OAuth source.

The repository contains source code and synthetic tests only. It ships with Reddit access disabled, contains no OAuth credentials, approval reference, user-generated content, database password, local runtime receipt, or real collection result.

This is an unofficial independent project. It is not endorsed by or affiliated
with Reddit, Inc. The MIT license covers the software only and grants no rights
to Reddit data or user content.

## What it does

- Uses only `https://oauth.reddit.com` endpoints allowed by the source profile.
- Enforces a minimum 20-second host interval, finite request budgets and immediate stop on HTTP 401, 403 or 429.
- Stores raw responses and full cleaned text outside Git with a maximum 48-hour retention window.
- Removes common personal data from safe snippets and exposes PostgreSQL readers only to restricted views.
- Keeps questions independent of optional community answers; answers never inflate the question count and remain `community_unverified`.
- Tracks requests, archives, manifests, versions, deletion events and transactional imports.
- Refuses HTML, RSS, public `.json`, proxy and logged-in-browser fallbacks.

## Access boundary

Do not enable `config/source.json` until Reddit has approved the exact use case and access mechanism. A permission or ticket reference is not an OAuth bearer token. Optional comment collection must be included in the approved scope before `approved_optional_comments` is enabled.

See [docs/SOURCE_POLICY.md](docs/SOURCE_POLICY.md) for the default policy assumptions. Re-check current Reddit terms and approval requirements before every deployment.

## Local setup

Requirements:

- Python 3.12
- PostgreSQL 17 or a compatible supported PostgreSQL release
- `psycopg[binary]==3.2.10`

Create an isolated environment and install the pinned dependency:

```sh
python3 -m venv .venv
.venv/bin/pip install -r requirements.lock
```

Create dedicated PostgreSQL roles and the `ctr_reddit` and `ctr_fixture` databases. `scripts/bootstrap_local.py` is intentionally fail-closed: it accepts only the configured data directory and a dedicated `ctr_admin` connection. Paths and the port are controlled through `CTR_STATE_ROOT`, `CTR_CONFIG_DIR`, `CTR_PRIVATE_ROOT`, `CTR_PGDATA`, `CTR_PGSOCKET` and `CTR_PGPORT`.

Keep runtime configuration outside the repository. Start from [config/local.example.json](config/local.example.json), save the real file under a private path and restrict it to the current user:

```sh
install -m 600 config/local.example.json "$HOME/.config/china-travel-reddit-research/local.json"
export CTR_CONFIG="$HOME/.config/china-travel-reddit-research/local.json"
```

Replace every placeholder locally. Never commit the resulting file or token file.

## Validation and bounded collection

Run the synthetic suite without Reddit network access:

```sh
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python scripts/check_delivery.py
```

The default test run executes the pure synthetic unit suite and skips
PostgreSQL integration cases. Set `CTR_FIXTURE_CONFIG` to a protected fixture
configuration to run all 27 tests.

After approval, configuration review and an explicit decision to enable the source profile, use the staged admission sequence:

```sh
.venv/bin/python -m ctr.cli collect --stage smoke --allow-network
.venv/bin/python -m ctr.cli collect --stage small1 --allow-network
.venv/bin/python -m ctr.cli collect --stage small2 --allow-network
.venv/bin/python -m ctr.cli campaign --max-rounds 10 --allow-network
.venv/bin/python -m ctr.cli audit
```

Add `--answers` consistently to every admission stage only when direct comments are explicitly approved. Fixture success is not evidence of live Reddit access or real-data quality.

## Data handling

PostgreSQL is the sole authoritative state store. Private archive, payload and manifest files live under `private_root`; safe queries use `safe_questions`, `safe_answers` and `safe_statistics`. Do not send raw content or private files to remote models. Run `sync` and `expire` at least hourly while real content is retained, and continue expiry even when source access is unavailable.

No public dataset or website integration is included.

See [NOTICE.md](NOTICE.md) for the platform and data-rights notice and
[SECURITY.md](SECURITY.md) for private vulnerability reporting guidance.
