import hashlib
import os
import tarfile
from pathlib import Path

import pytest
from yarl import URL

from pulp_tofu.app.modules import (
    ArchiveSource,
    GitSource,
    ModuleSource,
    ModuleSourceError,
    pack,
    parse_location,
)

BASE = URL("https://registry.example/v1/modules/acme/vpc/aws/1.0.0/download")


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        (
            "git::https://github.com/acme/terraform-aws-vpc?ref=v1.0.0",
            GitSource(url="https://github.com/acme/terraform-aws-vpc", ref="v1.0.0", subdir=""),
        ),
        (
            "git::https://forge.example/infra/modules.git//vpc/aws?ref=0a1b2c",
            GitSource(
                url="https://forge.example/infra/modules.git", ref="0a1b2c", subdir="vpc/aws"
            ),
        ),
        (
            "gitlab.com/group/sub/terraform-aws-vpc?ref=v2",
            GitSource(url="https://gitlab.com/group/sub/terraform-aws-vpc", ref="v2", subdir=""),
        ),
        (
            "https://files.example/vpc-1.0.0.tar.gz?token=abc",
            ArchiveSource(
                url="https://files.example/vpc-1.0.0.tar.gz?token=abc", format="tar.gz", subdir=""
            ),
        ),
        (
            "https://files.example/download?archive=zip",
            ArchiveSource(url="https://files.example/download", format="zip", subdir=""),
        ),
        (
            "/archive/vpc.zip",
            ArchiveSource(url="https://registry.example/archive/vpc.zip", format="zip", subdir=""),
        ),
    ],
)
def test_locations_parse_as_go_getter_reads_them(location: str, expected: ModuleSource) -> None:
    assert parse_location(location, BASE) == expected


@pytest.mark.parametrize(
    "location",
    [
        "git::ssh://git@github.com/acme/vpc.git",
        "git::http://github.com/acme/vpc",
        "s3::https://bucket.s3.amazonaws.com/vpc.zip",
        "hg::https://hg.example/vpc",
        "https://files.example/vpc",
        "git::https://forge.example/repo.git//../etc?ref=main",
        "git::https://github.com/acme/vpc?sshkey=AAAA",
    ],
)
def test_unsupported_or_unsafe_locations_are_refused(location: str) -> None:
    with pytest.raises(ModuleSourceError):
        parse_location(location, BASE)


def _tree(root: Path) -> Path:
    (root / "modules").mkdir(parents=True)
    (root / "main.tf").write_text('variable "cidr" {}\n')
    (root / "modules" / "sub.tf").write_text("# sub\n")
    (root / ".git").mkdir()
    (root / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    return root


def test_packing_is_deterministic_and_leaves_out_git(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    first, second = tmp_path / "a.tar.gz", tmp_path / "b.tar.gz"
    pack(source, first)
    os.utime(source / "main.tf", (0, 1_000_000))
    pack(source, second)
    assert (
        hashlib.sha256(first.read_bytes()).digest() == hashlib.sha256(second.read_bytes()).digest()
    )
    with tarfile.open(first) as tar:
        assert tar.getnames() == ["main.tf", "modules", "modules/sub.tf"]


def test_links_outside_the_module_are_refused(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    (tmp_path / "secret").write_text("not module code\n")
    (source / "leak.tf").symlink_to(tmp_path / "secret")
    with pytest.raises(ModuleSourceError):
        pack(source, tmp_path / "out.tar.gz")


def test_links_inside_the_module_are_stored_as_files(tmp_path: Path) -> None:
    source = _tree(tmp_path / "src")
    (source / "alias.tf").symlink_to(source / "main.tf")
    pack(source, tmp_path / "out.tar.gz")
    with tarfile.open(tmp_path / "out.tar.gz") as tar:
        member = tar.getmember("alias.tf")
        assert member.isfile()
