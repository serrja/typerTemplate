# typerTemplate

Read [baseRules at the selected commit](https://raw.githubusercontent.com/serrja/baseRules/bafcb63ed94b9769a0ca098e5db4b33c55dbc1b9/docs/engineering.md) before the local contracts below. Pinned links are reading sources, not automatic
loaders or grants. Review source diff and change full-SHA links explicitly, then run
documentation checks. Local overrides cover only this repository's specialization.

This repository owns the Typer CLI specialization of pythonTemplate. Read
composition.toml and provenance before editing. Python follows PEP 8. Edit
overlay/ for CLI wiring, settings and CliRunner examples. Refresh pinned parent
source only through tools/compose_template.py; template/ and inherited tools are
assembled source. Do not add HTTP/Pokerlost behavior, a second parser or runtime
sibling dependencies. Keep callbacks thin, settings lazy, automatic operation
noninteractive and errors free of secret input. Use real Copier render/update,
CliRunner and separately installed wheel subprocess tests. Shared engineering
rules belong to pythonTemplate; this file only describes specialization boundaries.
