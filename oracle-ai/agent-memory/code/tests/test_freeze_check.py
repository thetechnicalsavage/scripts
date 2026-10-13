# v1.2 2026-10-10  Brief 12 tests for tools/freeze_check.py (PLAN 7.0): section 7 extraction, the real manifest, and
#                  a tampered file or section detected; happy and failure paths.
#                  v1.2: the pinned manifest sha256 refuses an edited manifest whose digests were updated.
#                  v1.1: Codex review: manifest integrity (deleted, duplicate, extra, malformed lines), CRLF, fenced
#                  headings. v1.0: first version.
import hashlib
import shutil

import pytest

import tools.freeze_check as fc


def test_section7_extraction():
    text = "# t\n## 6. x\na\n## 7. Pre\nb\n### 7.1 c\nd\n## 8. APEX\ne\n"
    assert fc.section7(text) == "## 7. Pre\nb\n### 7.1 c\nd\n"
    with pytest.raises(ValueError):
        fc.section7("## 7. only\nno end\n")
    with pytest.raises(ValueError):
        fc.section7("## 8. no start\n")


def test_real_manifest_verifies_if_frozen():
    if not fc.MANIFEST.exists():
        pytest.skip("not frozen yet")
    assert fc.verify() == []


def _copy_tree(tmp_path):
    code = tmp_path / "code"
    for rel in fc.FROZEN:
        (code / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(fc.CODE / rel, code / rel)
    plan = tmp_path / "PLAN.md"
    shutil.copy2(fc.PLAN, plan)
    manifest = code / "data" / "frozen" / "FREEZE.sha256"
    manifest.write_text(fc.render(fc.current(code, plan)), encoding="ascii")
    return code, plan, manifest


def test_verify_detects_a_changed_file_and_a_changed_section(tmp_path):
    code, plan, manifest = _copy_tree(tmp_path)
    assert fc.verify(manifest, code, plan) == []
    target = code / "data" / "frozen" / "c1_queries.jsonl"
    target.write_bytes(target.read_bytes() + b"\n")
    assert fc.verify(manifest, code, plan) == ["data/frozen/c1_queries.jsonl"]
    plan.write_text(plan.read_text(encoding="utf-8").replace("## 7.", "## 7. (edited)", 1), encoding="utf-8")
    assert fc.SECTION in fc.verify(manifest, code, plan)


def test_verify_reports_missing_manifest_and_missing_file(tmp_path):
    code, plan, manifest = _copy_tree(tmp_path)
    (code / "py" / "lib" / "stats.py").unlink()
    assert "py/lib/stats.py" in fc.verify(manifest, code, plan)
    assert fc.verify(tmp_path / "nope.sha256", code, plan) == ["<manifest missing>"]


def test_section7_ignores_fenced_headings_and_7x_subheadings():
    text = "## 6. a\n```\n## 7. fake\n## 8. fake\n```\n## 7. Real\n### 7.1 sub\n```sql\n## 8. inside\n```\nz\n## 8. End\n"
    assert fc.section7(text) == "## 7. Real\n### 7.1 sub\n```sql\n## 8. inside\n```\nz\n"


def test_crlf_changes_the_section_digest(tmp_path):
    a, b = tmp_path / "a.md", tmp_path / "b.md"
    a.write_bytes(b"## 7. X\nline\n## 8. Y\n")
    b.write_bytes(b"## 7. X\r\nline\r\n## 8. Y\r\n")
    assert fc.section7_digest(a) != fc.section7_digest(b)


@pytest.mark.parametrize("edit", ["delete", "duplicate", "extra", "malformed", "truncate"])
def test_verify_refuses_an_edited_manifest(tmp_path, edit):
    code, plan, manifest = _copy_tree(tmp_path)
    lines = manifest.read_text(encoding="ascii").splitlines(keepends=True)
    body = [x for x in lines if not x.startswith("#")]
    if edit == "delete":
        lines.remove(body[3])
    elif edit == "duplicate":
        lines.insert(len(lines) - 1, body[3])
    elif edit == "extra":
        lines.append("0" * 64 + "  data/frozen/other.jsonl\n")
    elif edit == "malformed":
        lines[-1] = "not a manifest line\n"
    else:
        lines = lines[:-1] + [lines[-1][:20]]
    manifest.write_text("".join(lines), encoding="ascii")
    assert fc.verify(manifest, code, plan) != []


def test_pinned_manifest_sha_refuses_a_consistently_edited_manifest(tmp_path):
    code, plan, manifest = _copy_tree(tmp_path)
    pin = hashlib.sha256(manifest.read_bytes()).hexdigest()
    assert fc.verify(manifest, code, plan, manifest_sha256=pin) == []
    target = code / "data" / "frozen" / "c3_queries.jsonl"
    target.write_bytes(target.read_bytes() + b"\n")
    manifest.write_text(fc.render(fc.current(code, plan)), encoding="ascii")   # attacker updates the digest too
    assert fc.verify(manifest, code, plan) == []                                # unpinned: passes
    assert fc.verify(manifest, code, plan, manifest_sha256=pin) == ["<manifest sha256 differs from the pinned value>"]
