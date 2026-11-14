# Development

Run `uv lock` once for a fresh project, then `make install` and `make ci-local`.
The Docker path uses the project's locked native tools without a neighboring
template checkout. `make check` runs quality and source tests; `make coverage`
reports those results without rerunning them. `make build` creates distributions,
`make verify` accepts the built result, and `make release-check` compares metadata
and source-bound artifact evidence. Installed acceptance and source tests are
different scenarios.

Ignored `coverage/` contains source SHA/dirty/content identity, commands, statuses,
tool versions, coverage/JUnit and artifact checksums. Coverage below 100% warns;
failed or unavailable mandatory checks do not become PASS. `OFFLINE=1` requires
prepared exact tool, dependency and acceptance inputs; the default blocks unknown
runtime inputs. See [versions](versioning.md) and [releases](releases.md).

CI checks pull requests into `test`, `dev` and `main` and every push to those
branches, including documentation changes. Push runs keep distinct concurrency
groups so a later push cannot cancel an earlier event's checks. Only superseded
pull-request checks are canceled. Contributions follow working branch → `test` →
`dev` → `main`; new working branches use one lowerCamelCase identifier.
