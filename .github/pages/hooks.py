"""MkDocs hooks for the GitHub Pages site.

``site:`` links outside this site go to pulpproject.org: the docs link pulpcore and other
Pulp components as pulp-docs does, but this site builds only pulp_tofu and its own root
pages.

Every example tab group covers both ways to call Pulp. A tab group is a run of
``=== "label"`` blocks at one indent; when its labels name a variant, it must have a
``pulp-cli`` tab that runs ``pulp`` and a ``REST API`` tab that runs ``curl``. Findings
are warnings, which fail ``--strict`` builds.
"""

import logging
import re
from dataclasses import dataclass, field

from mkdocs.plugins import event_priority
from mkdocs.structure.pages import Page

log = logging.getLogger("mkdocs.plugins.pulp_tofu_pages")

_KEPT = ("pulp_tofu/", "user/", "dev/", "help/")
_SITE_LINK = re.compile(r"\]\(site:([^)\s]*)\)")

# Each variant's label and a pattern its tab's code must match.
VARIANTS = {
    "pulp-cli": re.compile(r"(^|[\s$(])pulp [a-z]", re.MULTILINE),
    "REST API": re.compile(r"\bcurl\b"),
}
_HEADER = re.compile(r'^(?P<indent>\s*)=== "(?P<label>[^"]+)"\s*$')
_FENCE = re.compile(r"^\s*(```|~~~)")


def _rewrite(match: re.Match[str]) -> str:
    target = match.group(1)
    if target.startswith(_KEPT):
        return match.group(0)
    return f"](https://pulpproject.org/{target.lstrip('/')})"


@dataclass
class TabGroup:
    line: int
    indent: int
    tabs: list[tuple[str, list[str]]] = field(default_factory=list)


def tab_groups(markdown: str) -> list[TabGroup]:
    groups: list[TabGroup] = []
    current: TabGroup | None = None
    fence: str | None = None
    for n, line in enumerate(markdown.splitlines(), 1):
        header = _HEADER.match(line) if fence is None else None
        if header:
            indent = len(header["indent"])
            if current is None or current.indent != indent:
                current = TabGroup(n, indent)
                groups.append(current)
            current.tabs.append((header["label"], []))
            continue
        if current is not None:
            if fence is None and line.strip() and len(line) - len(line.lstrip()) <= current.indent:
                current = None
            else:
                current.tabs[-1][1].append(line)
        if opened := _FENCE.match(line):
            fence = None if fence == opened[1] else (fence or opened[1])
    return groups


def _check_variants(markdown: str, src: str) -> None:
    for group in tab_groups(markdown):
        labels = [label for label, _ in group.tabs]
        if not set(labels) & VARIANTS.keys():
            continue
        if missing := VARIANTS.keys() - set(labels):
            log.warning("%s:%d: example tabs lack %s", src, group.line, ", ".join(sorted(missing)))
        for label, body in group.tabs:
            pattern = VARIANTS.get(label)
            if pattern is None:
                log.warning("%s:%d: unknown example tab %r", src, group.line, label)
            elif not pattern.search("\n".join(body)):
                log.warning("%s:%d: tab %r has no %s command", src, group.line, label, label)


# Before mkdocs-site-urls, which resolves every remaining site: link.
@event_priority(100)
def on_page_markdown(markdown: str, page: Page, **_: object) -> str:
    _check_variants(markdown, page.file.src_uri)
    return _SITE_LINK.sub(_rewrite, markdown)
