from __future__ import annotations

import re
import hashlib

from markupsafe import Markup, escape

_DIFF_GIT_RE = re.compile(r"^diff --git (.+)$")
_PLUS_PATH_RE = re.compile(r"^\+\+\+ b/(.+)$")
_MINUS_PATH_RE = re.compile(r"^--- a/(.+)$")
_HUNK_RE = re.compile(r"^@@")
_ADD_RE = re.compile(r"^\+")
_DEL_RE = re.compile(r"^-")
_META_RE = re.compile(
    r"^(?:"
    r"index |"
    r"new file mode |"
    r"deleted file mode |"
    r"old mode |"
    r"new mode |"
    r"similarity index |"
    r"dissimilarity index |"
    r"rename from |"
    r"rename to |"
    r"copy from |"
    r"copy to |"
    r"Binary files .+ differ|"
    r"--- |"
    r"\+\+\+ "
    r")"
)


def diff_anchor_id(path: str) -> str:
    digest = hashlib.sha1(path.encode("utf-8", errors="replace")).hexdigest()[:12]
    return f"diff-{digest}"


def parse_numstat(numstat_text: str) -> dict[str, dict[str, int | bool]]:
    counts: dict[str, dict[str, int | bool]] = {}
    for line in numstat_text.splitlines():
        fields = line.split("\t", 2)
        if len(fields) != 3:
            continue

        added_raw, deleted_raw, file_path = fields
        if not file_path:
            continue

        if added_raw == "-" or deleted_raw == "-":
            counts[file_path] = {"added": 0, "deleted": 0, "binary": True}
            continue

        try:
            added = int(added_raw)
            deleted = int(deleted_raw)
        except ValueError:
            continue

        counts[file_path] = {"added": added, "deleted": deleted, "binary": False}
    return counts


def _empty_counts() -> dict[str, int | bool]:
    return {"added": 0, "deleted": 0, "binary": False}


def _format_counts(counts: dict[str, int | bool]) -> str:
    if counts.get("binary"):
        return (
            '<span class="change-counts" aria-label="Binary file changed">'
            '<span class="count-add">+/-</span>'
            "</span>"
        )
    added = int(counts.get("added", 0))
    deleted = int(counts.get("deleted", 0))
    return (
        '<span class="change-counts">'
        f'<span class="count-add">+{added}</span>'
        f'<span class="count-del">-{deleted}</span>'
        "</span>"
    )


def parse_status_rows(
    status_text: str,
    counts_by_path: dict[str, dict[str, int | bool]],
) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for line in status_text.splitlines():
        if not line:
            continue
        marker = line[:2]
        path_text = line[3:] if len(line) > 3 else line.strip()
        if marker == "??":
            rows.append({
                "marker": marker,
                "path": path_text,
                "anchor": "",
                "counts": None,
                "untracked": True,
            })
        else:
            anchor_path = path_text.rsplit(" -> ", 1)[-1]
            counts = counts_by_path.get(anchor_path) or counts_by_path.get(path_text) or _empty_counts()
            rows.append({
                "marker": marker,
                "path": path_text,
                "anchor": diff_anchor_id(anchor_path),
                "counts": counts,
                "untracked": False,
            })
    return rows


def _extract_filename(diff_git_line: str) -> str:
    m = _DIFF_GIT_RE.match(diff_git_line)
    if m:
        parts = m.group(1).split(" b/", 1)
        if len(parts) == 2:
            return parts[1]
        return m.group(1)
    return diff_git_line


def render_diff_html(
    diff_text: str,
    counts_by_path: dict[str, dict[str, int | bool]] | None = None,
) -> Markup:
    if not diff_text:
        return Markup("")

    counts_by_path = counts_by_path or {}
    lines = diff_text.splitlines()

    sections: list[list[str]] = []
    current: list[str] = []

    for line in lines:
        if _DIFF_GIT_RE.match(line):
            if current:
                sections.append(current)
            current = [line]
        else:
            current.append(line)

    if current:
        sections.append(current)

    parts: list[str] = []

    for section in sections:
        if not section:
            continue

        header_line = section[0]
        filename = _extract_filename(header_line)

        plus_path: str | None = None
        minus_path: str | None = None
        for line in section[1:]:
            if line.startswith("@@"):
                break
            if line.startswith("+++ "):
                pm = _PLUS_PATH_RE.match(line)
                if pm:
                    plus_path = pm.group(1)
                break
            mm = _MINUS_PATH_RE.match(line)
            if mm:
                minus_path = mm.group(1)

        if plus_path and plus_path != "/dev/null":
            display_name = plus_path
        elif minus_path:
            display_name = minus_path
        else:
            display_name = filename

        escaped_name = escape(display_name)
        anchor_id = diff_anchor_id(display_name)
        header_counts = _format_counts(counts_by_path.get(display_name, _empty_counts()))
        inner_lines: list[str] = []

        for line in section:
            esc = escape(line)
            if _DIFF_GIT_RE.match(line):
                inner_lines.append(
                    f'<span class="diff-meta">{esc}</span>'
                )
            elif _META_RE.match(line):
                inner_lines.append(
                    f'<span class="diff-meta">{esc}</span>'
                )
            elif _HUNK_RE.match(line):
                inner_lines.append(
                    f'<span class="diff-hunk">{esc}</span>'
                )
            elif _ADD_RE.match(line):
                inner_lines.append(
                    f'<span class="diff-add">{esc}</span>'
                )
            elif _DEL_RE.match(line):
                inner_lines.append(
                    f'<span class="diff-del">{esc}</span>'
                )
            else:
                inner_lines.append(
                    f'<span class="diff-ctx">{esc}</span>'
                )

        body = "".join(inner_lines)
        dialog_id = f"{anchor_id}-dialog"
        parts.append(
            f'<details open class="diff-file is-wrapped" id="{anchor_id}" data-file-path="{escaped_name}">'
            f'<summary class="diff-file-header">'
            f'<span class="diff-file-title">{escaped_name}</span>'
            f'<span class="diff-file-tools">'
            f'{header_counts}'
            f'<label class="wrap-toggle"><input type="checkbox" class="js-wrap-toggle" checked> wrap</label>'
            f'<button type="button" class="file-dialog-button" data-dialog-id="{dialog_id}">open</button>'
            f'</span>'
            f'</summary>'
            f'<pre class="diff-body">{body}</pre>'
            f'<dialog class="file-dialog" id="{dialog_id}">'
            f'<div class="dialog-bar">'
            f'<strong>{escaped_name}</strong>'
            f'<span class="dialog-tools">'
            f'{header_counts}'
            f'<label class="wrap-toggle"><input type="checkbox" class="js-dialog-wrap-toggle" checked> wrap</label>'
            f'<button type="button" class="dialog-close">close</button>'
            f'</span>'
            f'</div>'
            f'<pre class="diff-body">{body}</pre>'
            f'</dialog>'
            f'</details>'
        )

    return Markup("\n".join(parts))
