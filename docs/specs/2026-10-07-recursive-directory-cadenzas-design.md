# Recursive Directory Cadenzas — Design

**Status:** draft for composer review · 2026-10-07
**Implementation owner:** Nannerl, as her first conducted Marianne improvement
**Changes a shared contract:** yes — `InjectionItem`, used by every score's
`prelude` and `cadenzas`

## Problem

A directory cadenza injects only the immediate files of one directory.
`daemon/baton/prompt.py` (`_resolve_directory_cadenza`) and the preview path in
`validation/rendering.py` (`_resolve_injections_preview`) both do:

```python
files = sorted(f for f in dir_path.glob("*") if f.is_file())
```

Subdirectories are silently skipped. The current workaround
(`plugins/marianne/docs/ref/patterns.md`, "Directory cadenzas are NOT
recursive") is to add a preflight stage that flattens or copies a curated
subtree into one flat directory. That is extra machinery for every score that
wants a structured inbox, a findings tree or a skill pack, and a silent skip is
the failure mode composers report most: a nested file the author expected is
simply absent from the prompt with no error.

Top-level-only is also load-bearing. The persistent-agent cadenza model
depends on it: `active/` contributes exactly its four files while sibling
`directives/`, `findings/`, `decisions/` and `archive/` hold durable detail
precisely because they are not injected (`AGENTS/README.md`,
`plugins/marianne/docs/ref/modern-agents.md`). Changing the default would put
every agent's archive into its prompt.

## Decision

Add opt-in recursion to directory injection. The default stays immediate-files
only, byte-for-byte identical to today, so every existing score keeps its
current prompts and receipts.

### Configuration

New optional fields on `InjectionItem`, valid only with `directory:`:

```yaml
sheet:
  cadenzas:
    1:
      - directory: "/abs/agents/nannerl/cadenzas/personal/inbox"
        as: context
        required: true
        recursive: true          # default false
        max_depth: 3             # default 3 when recursive; 1 = immediate files
        include: ["**/*.md"]     # optional; default all files
        exclude: ["archive/**"]  # optional; applied after include
        max_total_bytes: 64000   # optional aggregate cap for this item
```

| Field | Type | Default | Rule |
|---|---|---|---|
| `recursive` | bool | `false` | Requires `directory`. `false` ignores the other new fields except `max_total_bytes`. |
| `max_depth` | int ≥ 1 | `3` | Depth 1 is the directory's own files. Only meaningful with `recursive: true`. |
| `include` | list[str] | `[]` (all) | Gitignore-style globs matched against the POSIX path relative to the root. |
| `exclude` | list[str] | `[]` | Same matching; evaluated after `include`; exclude wins. |
| `max_total_bytes` | int ≥ 1 or null | `null` | Cap on summed source bytes for this item. Also allowed when not recursive. |

`file:` items that set any of these fields are a config error (pydantic
`model_validator`, extending `exactly_one_source`).

### Traversal rules

1. **Deterministic order.** Collect candidate files, then sort by their
   relative POSIX path (`a.md` < `a/b.md` < `b.md`). This keeps prompt digests
   and context receipts stable across runs and filesystems.
2. **Hidden entries are skipped.** Any path component starting with `.`
   (for example `.marianne/`, `.git/`) is excluded unless an `include` pattern
   names it explicitly. This keeps lifecycle debt, receipts and VCS state out of
   prompts by default.
3. **Symlinks are not followed.** A symlinked directory is not descended into
   and a symlinked file is not injected; each is recorded as skipped. This rules
   out cycles and escapes from the declared root. The root itself may be a
   symlink, as today.
4. **Depth bound.** Directories deeper than `max_depth` are not descended into.
5. **Files only.** Sockets, FIFOs and devices are ignored, as `is_file()`
   already does.

### Budget enforcement

Today there is no size guard on directory injection; the 8,000-token budget is
a documented convention. Recursion makes the gap unsafe, so this change adds
an enforced cap:

- When `max_total_bytes` is set and the summed `source_bytes` of selected files
  exceed it:
  - `required: true` — the sheet fails before execution with
    `required injection directory exceeds max_total_bytes: <path> (<n> > <cap>)`.
  - `required: false` — inject files in sorted order until the next file would
    exceed the cap, record the rest as `skipped: over_budget`, and log a
    warning.
- When `recursive: true` and `max_total_bytes` is unset, apply a default cap of
  256,000 bytes. A composer who needs more sets it explicitly. Non-recursive
  items keep no default cap, preserving today's behavior.

Files are never partially injected.

### Prompt rendering

The per-file header changes only for nested files. Today it is
`--- Input: <name> ---`; a recursive item uses the relative path instead,
`--- Input: findings/2026-10-07.md ---`, so two files with the same basename in
different subdirectories stay distinguishable. Immediate files under a
recursive item and every file under a non-recursive item keep today's header.

Binary files keep today's read-with-your-tools stanza, with the relative path
in the header.

### Receipts

`_record_delivery` entries for files delivered through a recursive item add:

- `relative_path` — POSIX path from the declared root.
- `delivery_kind: directory-recursive-inline` (instead of `directory-inline`).

One summary entry per recursive item records `declared_path`, `resolved_root`,
`recursive: true`, `max_depth`, `include`, `exclude`, `selected_count`,
`selected_bytes`, and `skipped` as a list of `{relative_path, reason}` with
reasons `hidden`, `symlink`, `depth`, `excluded`, `over_budget`. Receipts keep
recording hashes, not content.

This is what lets a conductor prove which nested files reached a performer.

### Validator parity

`validation/rendering.py` must use the same selection function as the baton,
not a second copy. Extract one pure function:

```python
def select_directory_files(
    root: Path,
    *,
    recursive: bool,
    max_depth: int,
    include: Sequence[str],
    exclude: Sequence[str],
) -> tuple[list[Path], list[SkippedEntry]]: ...
```

Both `_resolve_directory_cadenza` and `_resolve_injections_preview` call it, so
`mzt validate` previews exactly what the baton injects. `mzt validate` also
reports the selected count, total bytes and any budget overrun as a warning
(or an error for `required: true`).

Glob matching uses a small in-tree matcher over relative POSIX paths:
`fnmatch` per path segment, with `**` matching zero or more whole segments and
a pattern without `/` matching a basename at any depth. `pathspec` appears in
`uv.lock` only as a transitive dependency of other packages and is not
declared in `pyproject.toml`; depending on it would make injection behavior
change whenever an unrelated dependency drops it. Adding it as a direct
dependency is an acceptable alternative if the reviewer prefers full gitignore
semantics, but it must then be declared.

## Compatibility

- **Policy:** `preserve`. With `recursive` absent or `false`, selection order,
  injected text, headers and receipt fields are identical to today. This is a
  test obligation, not an assumption.
- `max_total_bytes` on a non-recursive item is new opt-in behavior and changes
  nothing unless set.
- Generated persistent-agent scores (`plugins/marianne/agent-scores/`, compiler
  output) do not change. Their `active/` attachments stay non-recursive. No
  package regeneration is required.
- `extra="forbid"` on `InjectionItem` means older Marianne versions reject a
  score using the new fields with a clear schema error, which is the correct
  failure.

## Proof obligations

Tests (all new, in the existing `tests/` layout):

1. **Default is unchanged.** A golden test over a nested fixture: a
   non-recursive item selects only immediate files, with byte-identical prompt
   text and receipt entries compared to the pre-change output.
2. Recursive selection order is relative-path sorted, at depths 1–3.
3. `max_depth` bounds descent.
4. Hidden directories and files are skipped unless explicitly included.
5. Symlinked files and directories are not followed and appear as skipped.
6. `include` and `exclude` semantics, with exclude winning.
7. Budget: required over-cap fails before execution; optional over-cap injects
   a sorted prefix and records `over_budget`; the recursive default cap applies.
8. Nested headers use the relative path; same-basename files stay distinct.
9. Receipts carry `relative_path`, `directory-recursive-inline`, and the
   per-item summary with skip reasons.
10. Validator parity: for the same fixture, the validate preview and the baton
    select the same files in the same order.
11. Config errors: new fields on a `file:` item; `max_depth < 1`;
    `max_total_bytes < 1`.

Then one full-suite run on the candidate checkout, with import provenance, and
a live smoke: one real score attaching a nested directory, with its context
receipt checked against the expected file list.

## Documentation

- `plugins/marianne/docs/ref/patterns.md` — replace "Directory cadenzas are NOT
  recursive" with the default rule plus the opt-in, keeping the
  flatten-with-preflight pattern as an alternative.
- `docs/configuration-reference.md` — add the five fields to the
  `InjectionItem` table.
- `plugins/marianne/skills/score-authoring/SKILL.md` — update the
  "Directory cadenzas are NOT recursive" sentence to "not recursive by default".
- `plugins/marianne/docs/ref/modern-agents.md` — state that `active/` remains a
  non-recursive attachment by design.

## Out of scope

- Changing the default to recursive.
- Content-aware budgeting in tokens rather than bytes. Bytes are deterministic
  and provider-independent; token estimates are not.
- Watching directories for changes mid-sheet. Cadenzas stay reread per sheet.
- An agent inbox convention (`05-commission.md`). That is a separate
  coordination decision and does not need recursion.

## Delivery as Nannerl's first conducted improvement

This change is small enough to finish and large enough to test the whole loop:
a shared contract, a receipt format, validator parity and documentation. The
intended path is that Nannerl commissions it through Marianne rather than
editing source herself: a builder for construction and tests, an independent
reviewer, bounded repair, the full suite and a live smoke, then her own record
of what the engagement taught her about conducting Marianne. Until Nannerl
exists, this spec waits as the first entry in her improvement ledger.
