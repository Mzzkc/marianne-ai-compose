# Workspaces

Marianne score workspaces live here — created on demand at run time and
gitignored by design. This README exists only so the directory is present
on a fresh clone (path validation expects it; scores use relative
`workspace:` paths anchored under this directory).

System scores that operate on the repository use `workspaces/<name>`
(relative to the score file). Personal/utility scores should omit
`workspace:` and let Marianne auto-derive under `~/workspaces/` (#58).
