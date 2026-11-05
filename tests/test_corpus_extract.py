"""Repository extraction preserves YAML bytes and excludes nested repositories."""

from marianne.corpus import extract_in_repo


def test_extract_is_sorted_and_preserves_source_bytes(tmp_path):
    root = tmp_path / "examples"
    category = root / "creative"
    category.mkdir(parents=True)
    texts = {"z.yaml": "name: café\r\n# why\r\n", "a.yml": "name: alpha\n"}
    for name, text in texts.items():
        (category / name).write_bytes(text.encode())
    (category / "ignore.yaml.lock").write_text("locked")
    records = extract_in_repo([root, category])
    assert [record.output for record in records] == [texts["a.yml"], texts["z.yaml"]]
    assert all(record.provenance.category == "creative" for record in records)
    assert all(record.provenance.source_kind == "in_repo" for record in records)
    assert records[0].provenance.source_path == str(category / "a.yml")
    assert extract_in_repo([category, root]) == records


def test_extract_skips_submodules_git_dirs_and_symlinks(tmp_path):
    root = tmp_path / "scores"
    root.mkdir()
    (root / "kept.yaml").write_text("name: kept\n")
    for name, directory in (("submodule", False), ("nested-repo", True)):
        nested = root / name
        nested.mkdir()
        marker = nested / ".git"
        if directory:
            marker.mkdir()
        else:
            marker.write_text("gitdir: elsewhere")
        (nested / "private.yaml").write_text("name: excluded\n")
        assert extract_in_repo([nested]) == []
    external = tmp_path / "external.yaml"
    external.write_text("name: external\n")
    (root / "linked.yaml").symlink_to(external)
    records = extract_in_repo([root])
    assert [record.output for record in records] == ["name: kept\n"]
