"""MkDocs hook: send ``site:`` links outside this site to pulpproject.org.

The docs link pulpcore and other Pulp components as pulp-docs does, with ``site:``.
This site builds only pulp_tofu and its own root pages, so those links would point at
pages it does not have.
"""

import re

from mkdocs.plugins import event_priority

_KEPT = ("pulp_tofu/", "user/", "dev/", "help/")
_SITE_LINK = re.compile(r"\]\(site:([^)\s]*)\)")


def _rewrite(match: re.Match[str]) -> str:
    target = match.group(1)
    if target.startswith(_KEPT):
        return match.group(0)
    return f"](https://pulpproject.org/{target.lstrip('/')})"


# Before mkdocs-site-urls, which resolves every remaining site: link.
@event_priority(100)
def on_page_markdown(markdown: str, **_: object) -> str:
    return _SITE_LINK.sub(_rewrite, markdown)
