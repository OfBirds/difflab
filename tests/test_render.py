from __future__ import annotations

import pytest

from difflab.render import diff_anchor_id, parse_numstat, parse_status_rows, render_diff_html

SAMPLE_DIFF = """\
diff --git a/foo.py b/foo.py
index abc123..def456 100644
--- a/foo.py
+++ b/foo.py
@@ -1,4 +1,5 @@
 context line
-removed line
+added line
+another added
 more context
@@ -10,3 +11,3 @@
 ctx
-old
+new
"""

TWO_FILE_DIFF = """\
diff --git a/alpha.py b/alpha.py
index 111..222 100644
--- a/alpha.py
+++ b/alpha.py
@@ -1,2 +1,2 @@
-old alpha
+new alpha
diff --git a/beta.py b/beta.py
index 333..444 100644
--- a/beta.py
+++ b/beta.py
@@ -1,2 +1,2 @@
-old beta
+new beta
"""

SCRIPT_DIFF = """\
diff --git a/hack.js b/hack.js
index 000..111 100644
--- a/hack.js
+++ b/hack.js
@@ -1,1 +1,1 @@
+<script>alert(1)</script>
"""

DELETION_DIFF = """\
diff --git a/gone.py b/gone.py
deleted file mode 100644
index abc..000
--- a/gone.py
+++ /dev/null
@@ -1,1 +0,0 @@
-deleted content
"""


def test_classes_present_in_diff():
    html = render_diff_html(SAMPLE_DIFF)
    assert "diff-file" in html
    assert 'class="diff-file-header"' in html
    assert 'class="diff-hunk"' in html
    assert 'class="diff-add"' in html
    assert 'class="diff-del"' in html


def test_two_file_diff_two_details():
    html = render_diff_html(TWO_FILE_DIFF)
    assert html.count('<details') == 2
    assert "alpha.py" in html
    assert "beta.py" in html


def test_script_tag_escaped():
    html = render_diff_html(SCRIPT_DIFF)
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_empty_input_returns_empty():
    assert render_diff_html("") == ""


def test_devnull_uses_a_path():
    html = render_diff_html(DELETION_DIFF)
    assert "gone.py" in html


def test_meta_lines_get_diff_meta():
    html = render_diff_html(SAMPLE_DIFF)
    assert 'class="diff-meta"' in html
    # index line, --- line, +++ line should all be diff-meta
    assert html.count('class="diff-meta"') >= 3


DELETION_TRICKY_DIFF = """\
diff --git a/gone.py b/gone.py
deleted file mode 100644
index abc..000
--- a/gone.py
+++ /dev/null
@@ -1,3 +0,0 @@
-content line
--- a/fake.py
-more content
"""


def test_deletion_header_not_corrupted_by_body_content():
    """A removed line whose content is '-- a/fake.py' (appears as '--- a/fake.py'
    in the diff) must not overwrite the real filename gone.py."""
    html = render_diff_html(DELETION_TRICKY_DIFF)
    assert 'data-file-path="gone.py"' in html
    assert '<span class="diff-file-title">gone.py</span>' in html


def test_no_double_spacing_between_lines():
    """Spans must not be separated by bare newlines (would double-space in pre)."""
    html = render_diff_html(SAMPLE_DIFF)
    assert '>\n<' not in str(html)


# ── New feature tests ────────────────────────────────────────────────────────

NUMSTAT_TEXT = "3\t1\talpha.py\n0\t5\tbeta.py\n-\t-\timage.png\n"


def test_parse_numstat_basic():
    counts = parse_numstat(NUMSTAT_TEXT)
    assert counts["alpha.py"] == {"added": 3, "deleted": 1, "binary": False}
    assert counts["beta.py"] == {"added": 0, "deleted": 5, "binary": False}


def test_parse_numstat_binary():
    counts = parse_numstat(NUMSTAT_TEXT)
    assert counts["image.png"]["binary"] is True


def test_parse_numstat_empty():
    assert parse_numstat("") == {}


def test_parse_numstat_ignores_malformed():
    counts = parse_numstat("not\ta\tvalid\textra\n1\t2\tgood.py\n")
    assert "good.py" in counts


def test_diff_anchor_id_stable():
    a1 = diff_anchor_id("foo/bar.py")
    a2 = diff_anchor_id("foo/bar.py")
    assert a1 == a2
    assert a1.startswith("diff-")


def test_diff_anchor_id_different_paths():
    assert diff_anchor_id("a.py") != diff_anchor_id("b.py")


def test_parse_status_rows_basic():
    counts = {"foo.py": {"added": 2, "deleted": 1, "binary": False}}
    rows = parse_status_rows(" M foo.py\n?? bar.py\n", counts)
    assert len(rows) == 2
    assert rows[0]["marker"] == " M"
    assert rows[0]["path"] == "foo.py"
    assert rows[0]["counts"]["added"] == 2
    assert rows[1]["path"] == "bar.py"
    assert rows[1]["untracked"] is True
    assert rows[1]["counts"] is None


def test_parse_status_rows_anchor_matches_diff_anchor():
    counts = {"foo.py": {"added": 1, "deleted": 0, "binary": False}}
    rows = parse_status_rows(" M foo.py\n", counts)
    assert rows[0]["anchor"] == diff_anchor_id("foo.py")


def test_render_diff_shows_counts():
    counts = {"foo.py": {"added": 7, "deleted": 3, "binary": False}}
    html = render_diff_html(SAMPLE_DIFF, counts_by_path=counts)
    assert "+7" in html
    assert "-3" in html


def test_render_diff_binary_shows_plus_minus():
    binary_counts = {"foo.py": {"added": 0, "deleted": 0, "binary": True}}
    html = render_diff_html(SAMPLE_DIFF, counts_by_path=binary_counts)
    assert "+/-" in html


def test_render_diff_has_wrap_checkbox_checked():
    html = render_diff_html(SAMPLE_DIFF)
    assert 'class="js-wrap-toggle" checked' in html


def test_render_diff_starts_wrapped():
    html = render_diff_html(SAMPLE_DIFF)
    assert "is-wrapped" in html


def test_render_diff_has_dialog():
    html = render_diff_html(SAMPLE_DIFF)
    assert '<dialog class="file-dialog"' in html
    assert 'class="dialog-close"' in html


def test_render_diff_anchor_id_in_html():
    html = render_diff_html(SAMPLE_DIFF)
    anchor = diff_anchor_id("foo.py")
    assert f'id="{anchor}"' in html


# ── Untracked file handling ──────────────────────────────────────────────────

def test_parse_status_rows_untracked_flag():
    rows = parse_status_rows("?? newfile.txt\n", {})
    assert len(rows) == 1
    assert rows[0]["untracked"] is True
    assert rows[0]["marker"] == "??"
    assert rows[0]["path"] == "newfile.txt"


def test_parse_status_rows_untracked_counts_is_none():
    rows = parse_status_rows("?? newfile.txt\n", {})
    assert rows[0]["counts"] is None


def test_parse_status_rows_untracked_anchor_empty():
    rows = parse_status_rows("?? newfile.txt\n", {})
    assert rows[0]["anchor"] == ""


def test_parse_status_rows_tracked_untracked_flag():
    counts = {"foo.py": {"added": 1, "deleted": 0, "binary": False}}
    rows = parse_status_rows(" M foo.py\n", counts)
    assert rows[0]["untracked"] is False


def test_parse_status_rows_mixed_tracked_and_untracked():
    counts = {"foo.py": {"added": 3, "deleted": 1, "binary": False}}
    rows = parse_status_rows(" M foo.py\n?? orphan.log\n", counts)
    assert rows[0]["untracked"] is False
    assert rows[0]["counts"]["added"] == 3
    assert rows[1]["untracked"] is True
    assert rows[1]["counts"] is None


# ── Counts derivation (used by index file_count and diff heading) ─────────────

def test_parse_numstat_total_added_deleted():
    counts = parse_numstat("3\t1\talpha.py\n2\t4\tbeta.py\n")
    total_added = sum(c["added"] for c in counts.values() if not c.get("binary"))
    total_deleted = sum(c["deleted"] for c in counts.values() if not c.get("binary"))
    assert total_added == 5
    assert total_deleted == 5


def test_parse_numstat_total_excludes_binary():
    counts = parse_numstat("5\t2\ttext.py\n-\t-\timage.png\n")
    total_added = sum(c["added"] for c in counts.values() if not c.get("binary"))
    total_deleted = sum(c["deleted"] for c in counts.values() if not c.get("binary"))
    assert total_added == 5
    assert total_deleted == 2


def test_status_row_file_count_includes_untracked():
    counts = {"tracked.py": {"added": 1, "deleted": 0, "binary": False}}
    rows = parse_status_rows(" M tracked.py\n?? new.txt\n?? other.log\n", counts)
    assert len(rows) == 3
    assert sum(1 for r in rows if r["untracked"]) == 2
    assert sum(1 for r in rows if not r["untracked"]) == 1


def test_parse_numstat_zero_counts():
    counts = parse_numstat("0\t0\tempty.py\n")
    assert counts["empty.py"] == {"added": 0, "deleted": 0, "binary": False}
    total_added = sum(c["added"] for c in counts.values() if not c.get("binary"))
    assert total_added == 0
