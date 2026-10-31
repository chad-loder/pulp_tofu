"""Provider version numbers, read as SemVer 2.0 the way OpenTofu reads them.

``semantic_version`` parses and orders them, as it does for pulp_ansible. It ships no
type information, so ``typings/semantic_version`` stubs what this module, its only
importer, uses.
"""

from collections.abc import Sequence

from semantic_version import Version


def is_release_version(value: str) -> bool:
    """Whether ``value`` is a SemVer 2.0 version without build metadata.

    Build metadata is refused: OpenTofu orders ``1.0.0+a`` and ``1.0.0+b`` as one
    version, so two releases could claim the same place.
    """
    parsed = _parse(value)
    return parsed is not None and not parsed.build


def latest_releases(versions: Sequence[str], count: int) -> list[str]:
    """The ``count`` highest versions without a pre-release or build part, newest first.

    OpenTofu leaves pre-releases out when it resolves an unconstrained provider.
    """
    releases = [
        (parsed, value)
        for value in versions
        if (parsed := _parse(value)) is not None and not parsed.prerelease and not parsed.build
    ]
    releases.sort(reverse=True)
    return [value for _, value in releases[:count]]


def _parse(value: str) -> Version | None:
    try:
        return Version(value)
    except ValueError:
        return None
