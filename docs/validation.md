# Purpose and validation

Validated on 28 October 2026 with Python 3.9 locally. CI also exercises Python 3.12 and 3.13.

## Does it work for its purpose?

Yes, for cooperative local task coordination. Independent clients can exchange
messages, see execution state, and acquire exclusive resource leases before
editing. A rejected claimant cannot transition the task through the API.

It does not automatically make arbitrary coding tools cooperate. Each tool must
call the CLI or Python client, use the same resource naming scheme, and stop work
if its lease expires or renewal fails. The service cannot prevent an agent that
ignores the protocol from modifying files or making a Git commit. Agent names
are labels within a shared trust domain, not authenticated individual identities.

## Reproducible evidence

Run `python3 -m unittest discover -s tests -v` from the repository root.

The acceptance test `test_real_worker_processes_and_git_commit` starts a real
HTTP server and creates a temporary Git repository. One client holds an auth
resource lease. A separate Python worker tries to claim the overlapping source
resource: it receives HTTP 409 and exits without creating its proposed file.
Another invocation claims a disjoint tests resource, writes a real file, sends a
handoff message, and completes its task. The first client finishes its edit,
reserves the shared Git integration key, and commits both accepted files. The
test checks the Git tree contains exactly those files and no rejected edit.

Additional tests verify:

- Exactly one winner among competing claims, including independent SQLite connections.
- Parent/child overlap rejection after normalizing equivalent resource keys.
- Expiry, reclaim fencing increments, and rejection of stale ownership tokens.
- State persistence, authentication, Host and Origin rejection, and input validation.
- Local client credentials are not forwarded through HTTP redirects.

These are simulated cooperating workers, not tests of third-party model products
or vendor integrations. They establish the protocol behavior and actual file/Git
workflow. They do not establish model reliability, adoption, or production scale.

## Improvements from this review

- Resource aliases such as `repo//src/./auth/` now resolve to `repo/src/auth`.
- Ambiguous traversal, absolute paths, backslashes and drive prefixes are rejected.
- SQLite transactions arbitrate claims rather than relying solely on a process mutex.
- Expired tasks appear available on the dashboard instead of remaining visually running.
- The importable client provides a direct integration point for agent tools.
- CLI file/stdin input keeps lease tokens out of command-line arguments.
- Client requests bypass environment proxies and reject redirects.
- HTTP connections have a read timeout; optional name/status/recipient fields are validated.

## Remaining boundaries

Use separate worktrees for simultaneous coding and one shared integration lease
around Git operations. Long operations must renew their leases and stop on failure.
The API supplies fencing numbers, but external filesystems and Git do not enforce
them. Per-agent credentials, automatic adapters, distributed operation, task
pagination and archival remain future work. This is a local MVP for trusted
participants, not a sandbox or a distributed transaction manager.
