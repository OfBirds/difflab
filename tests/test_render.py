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
    assert 'class="diff-file is-wrapped"' in html
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
    assert '<span class="diff-file-title">gone.py</span>' in html


def test_no_double_spacing_between_lines():
    """Spans must not be separated by bare newlines (would double-space in pre)."""
    html = render_diff_html(SAMPLE_DIFF)
    assert '>\n<' not in str(html)


def test_parse_numstat_counts_and_binary():
    counts = parse_numstat("3\t2\tfoo.py\n-\t-\timage.png\nbad\t2\tnope\n")
    assert counts["foo.py"] == {"added": 3, "deleted": 2, "binary": False}
    assert counts["image.png"] == {"added": 0, "deleted": 0, "binary": True}
    assert "nope" not in counts


def test_render_diff_counts_and_anchor_id():
    counts = parse_numstat("3\t2\tfoo.py\n")
    html = render_diff_html(SAMPLE_DIFF, counts_by_path=counts)
    assert f'id="{diff_anchor_id("foo.py")}"' in html
    assert '<span class="count-add">+3</span>' in html
    assert '<span class="count-del">-2</span>' in html
    assert 'class="js-wrap-toggle" checked' in html
    assert '<dialog class="file-dialog"' in html


def test_status_rows_use_counts_and_anchor():
    counts = parse_numstat("4\t1\tsrc/app.py\n")
    rows = parse_status_rows(" M src/app.py\n?? notes.txt\n", counts)
    assert rows[0]["marker"] == " M"
    assert rows[0]["path"] == "src/app.py"
    assert rows[0]["anchor"] == diff_anchor_id("src/app.py")
    assert rows[0]["counts"] == {"added": 4, "deleted": 1, "binary": False}
    assert rows[1]["counts"] == {"added": 0, "deleted": 0, "binary": False}
