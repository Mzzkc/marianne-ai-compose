"""Generate the code reference pages and navigation."""

from pathlib import Path

import mkdocs_gen_files

nav = mkdocs_gen_files.Nav()

root = Path("src")
ref_dir = Path("reference")

for path in sorted(root.rglob("*.py")):
    module_path = path.relative_to(root).with_suffix("")
    doc_path = path.relative_to(root).with_suffix(".md")
    full_doc_path = ref_dir / doc_path

    parts = tuple(module_path.parts)

    # Skip internal/private modules
    if any(part.startswith("_") and part != "__init__" for part in parts):
        continue

    # Skip non-code directories (e.g. YAML template directories, static assets)
    SKIP_DIRS = {"templates", "static"}
    if any(part in SKIP_DIRS for part in parts):
        continue

    # Skip __init__ from nav display but still generate the page
    if parts[-1] == "__init__":
        parts = parts[:-1]
        doc_path = doc_path.with_name("index.md")
        full_doc_path = full_doc_path.with_name("index.md")

    if not parts:
        continue

    nav_parts = tuple(parts)
    nav[nav_parts] = doc_path.as_posix()

    with mkdocs_gen_files.open(full_doc_path, "w") as fd:
        ident = ".".join(parts)
        fd.write(f"::: {ident}\n")

    mkdocs_gen_files.set_edit_path(full_doc_path, path.as_posix())

with mkdocs_gen_files.open(ref_dir / "SUMMARY.md", "w") as nav_file:
    nav_file.writelines(nav.build_literate_nav())
