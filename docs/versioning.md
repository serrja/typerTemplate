# Version and release boundaries

Root `[project].version` in `pyproject.toml` owns this template's PEP 440 version.
The compositor derives stamps from it. Generated application versions remain in
their own metadata; `.template-version.json` records the template version and
Copier's supported full source commit/ref. Copier owns answers. A template update
preserves application code, settings, tests, migrations and version.

For compatibility decisions and annotated tags follow the
[shared version policy](https://github.com/serrja/baseRules/blob/main/docs/versioning.md)
and [release policy](https://github.com/serrja/baseRules/blob/main/docs/releases.md).
Run `make ci-local` and `make release-check`, review the exact commit, integrate
only with successful required checks, then inspect tag-triggered workflows before
sending one authorized tag. Publication and deployment remain separate actions.
A billing-blocked PR cannot be merged or tagged as a checked release.

Helm blueprints, where present, retain chart `version` and application `appVersion`
separately from this source bundle version; database revisions are unchanged.
