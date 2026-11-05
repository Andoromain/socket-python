"""Cohérence des numéros de version entre le paquet et l'exécutable Windows."""

import re
from pathlib import Path

from socket_chat import __version__

ROOT = Path(__file__).resolve().parents[1]


def test_windows_version_info_matches_package():
    text = (ROOT / "packaging" / "windows" / "version_info.txt").read_text(encoding="utf-8")
    numbers = tuple(int(n) for n in __version__.split("."))
    expected = (*numbers, 0)[:4]
    for found in re.findall(r"(?:filevers|prodvers)=\(([\d, ]+)\)", text):
        assert tuple(int(n) for n in found.split(",")) == expected
    for found in re.findall(r"StringStruct\('(?:File|Product)Version', '([^']+)'\)", text):
        assert found == __version__


def test_resources_present():
    resources = ROOT / "src" / "socket_chat" / "resources"
    for name in ("icon.svg", "icon.png", "icon.ico"):
        assert (resources / name).is_file()
