import io
import tarfile

import pytest

from pulp_tofu.app.protocol import is_module_name, is_module_system
from pulp_tofu.app.publishing import ReleaseError, check_module_archive


def _tarball(*members: tarfile.TarInfo, data: bytes = b'variable "x" {}\n') -> io.BytesIO:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for member in members:
            if member.isfile():
                member.size = len(data)
                tar.addfile(member, io.BytesIO(data))
            else:
                tar.addfile(member)
    buffer.seek(0)
    return buffer


def _file(name: str) -> tarfile.TarInfo:
    return tarfile.TarInfo(name)


def _of_type(name: str, kind: bytes) -> tarfile.TarInfo:
    member = tarfile.TarInfo(name)
    member.type = kind
    member.linkname = "/etc/passwd"
    return member


def test_module_directory_is_accepted() -> None:
    directory = tarfile.TarInfo("modules")
    directory.type = tarfile.DIRTYPE
    check_module_archive(_tarball(_file("main.tf"), directory, _file("modules/sub.tf")))


@pytest.mark.parametrize(
    "member",
    [
        _file("../escape.tf"),
        _file("/etc/main.tf"),
        _file("a/../../escape.tf"),
        _of_type("link.tf", tarfile.SYMTYPE),
        _of_type("hard.tf", tarfile.LNKTYPE),
        _of_type("fifo", tarfile.FIFOTYPE),
    ],
    ids=["parent", "absolute", "nested parent", "symlink", "hardlink", "fifo"],
)
def test_entries_that_leave_the_module_or_are_not_files_are_refused(
    member: tarfile.TarInfo,
) -> None:
    with pytest.raises(ReleaseError):
        check_module_archive(_tarball(_file("main.tf"), member))


def test_a_package_that_is_not_gzip_tar_is_refused() -> None:
    with pytest.raises(ReleaseError):
        check_module_archive(io.BytesIO(b"PK\x03\x04 not a tarball"))


@pytest.mark.parametrize(
    ("name", "valid"),
    [("vpc", True), ("My_Module-2", True), ("_vpc", False), ("vpc-", False), ("a" * 65, False)],
)
def test_module_names_follow_opentofu_rules(name: str, valid: bool) -> None:
    assert is_module_name(name) is valid


@pytest.mark.parametrize(("system", "valid"), [("aws", True), ("AWS", False), ("a-b", False)])
def test_module_systems_follow_opentofu_rules(system: str, valid: bool) -> None:
    assert is_module_system(system) is valid
