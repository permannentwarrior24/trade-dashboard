"""Security boundaries shared by the dashboard API and analyzers."""

from __future__ import annotations

import html
import re
from html.parser import HTMLParser


SYMBOL_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
REPORT_ID_PATTERN = re.compile(
    r"^\d{8}_\d{6}(?:_\d{6})?_[A-Za-z0-9._-]{1,64}(?:_[0-9a-f]{8})?$"
)

ALLOWED_REPORT_TAGS = {
    "br", "div", "em", "h3", "h4", "h5", "hr", "li", "p", "span",
    "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
}
VOID_TAGS = {"br", "hr"}
DROP_WITH_CONTENT = {"iframe", "math", "object", "script", "style", "svg", "template"}
CLASS_TOKEN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def validate_symbol(value: str) -> str:
    """Accept exchange symbols only, excluding path and prompt-control characters."""
    if not SYMBOL_PATTERN.fullmatch(value):
        raise ValueError(
            "symbol must be 1-64 ASCII letters, digits, dots, underscores, or hyphens"
        )
    return value


def validate_report_id(value: str) -> str:
    """Validate both legacy and current report IDs before filesystem access."""
    if not REPORT_ID_PATTERN.fullmatch(value):
        raise ValueError("invalid report ID")
    return value


def validate_secret(value: str) -> str:
    """Reject values that can alter the line-oriented .env file."""
    if not value or len(value) > 512:
        raise ValueError("credential must contain 1-512 characters")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("credential must not contain control characters")
    return value


class _ReportHTMLSanitizer(HTMLParser):
    """Allow only the deliberately limited report presentation vocabulary."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._dropped_tags: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._dropped_tags:
            if tag in DROP_WITH_CONTENT:
                self._dropped_tags.append(tag)
            return
        if tag in DROP_WITH_CONTENT:
            self._dropped_tags.append(tag)
            return
        if tag not in ALLOWED_REPORT_TAGS:
            return

        safe_attrs = ""
        for name, value in attrs:
            if name.lower() != "class" or not value:
                continue
            classes = [token for token in value.split() if CLASS_TOKEN.fullmatch(token)]
            if classes:
                safe_attrs = f' class="{html.escape(" ".join(classes), quote=True)}"'
            break
        self.parts.append(f"<{tag}{safe_attrs}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if self._dropped_tags or tag in DROP_WITH_CONTENT:
            return
        self.handle_starttag(tag, attrs)
        if tag in ALLOWED_REPORT_TAGS and tag not in VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self._dropped_tags:
            if tag == self._dropped_tags[-1]:
                self._dropped_tags.pop()
            return
        if tag in ALLOWED_REPORT_TAGS and tag not in VOID_TAGS:
            self.parts.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self._dropped_tags:
            self.parts.append(html.escape(data, quote=False))


def sanitize_report_html(value: str) -> str:
    """Return report HTML containing only inert presentation elements."""
    sanitizer = _ReportHTMLSanitizer()
    sanitizer.feed(value)
    sanitizer.close()
    cleaned = "".join(sanitizer.parts)

    def add_even_rows(match: re.Match[str]) -> str:
        rows = re.findall(
            r"(<tr(?:\s[^>]*)?>.*?</tr>)",
            match.group(1),
            flags=re.DOTALL | re.IGNORECASE,
        )
        for index, row in enumerate(rows):
            if index % 2 == 1 and "class=" not in row.split(">", 1)[0]:
                rows[index] = re.sub(r"^<tr", '<tr class="even"', row, count=1)
        return f"<tbody>{''.join(rows)}</tbody>" if rows else match.group(0)

    return re.sub(
        r"<tbody>(.*?)</tbody>",
        add_even_rows,
        cleaned,
        flags=re.DOTALL | re.IGNORECASE,
    )
