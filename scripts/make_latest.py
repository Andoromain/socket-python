"""Génère ``latest.json`` (manifeste de mise à jour) à partir des fichiers construits.

Usage : python scripts/make_latest.py --dist dist [--tag v3.2.0]

Le manifeste liste, pour chaque plate-forme trouvée, le nom, la taille et l'empreinte
SHA-256 de l'installeur, ainsi que les notes de la version tirées de CHANGELOG.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

#: Plate-forme -> modèle de nom du fichier publié.
ASSETS = {
    "windows-x64": "socket-chat-{version}-windows-setup.exe",
    "linux-x86_64": "socket-chat-{version}-linux-x86_64.tar.gz",
}


def package_version(root: Path = ROOT) -> str:
    text = (root / "src" / "socket_chat" / "__init__.py").read_text(encoding="utf-8")
    match = re.search(r'__version__\s*=\s*"([^"]+)"', text)
    if match is None:
        raise SystemExit("__version__ introuvable.")
    return match.group(1)


def release_notes(changelog: Path, version: str) -> str:
    """Section « ## <version> » du journal des modifications (sans le titre)."""
    text = changelog.read_text(encoding="utf-8")
    match = re.search(rf"^## {re.escape(version)}\s*$(.*?)(?=^## |\Z)", text, re.M | re.S)
    return match.group(1).strip() if match else ""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def build_manifest(
    dist: Path, version: str, changelog: Path, published: str | None = None
) -> dict[str, object]:
    assets: dict[str, dict[str, object]] = {}
    for key, pattern in ASSETS.items():
        path = dist / pattern.format(version=version)
        if path.is_file():
            assets[key] = {"name": path.name, "size": path.stat().st_size, "sha256": sha256(path)}
    if not assets:
        raise SystemExit(f"Aucun installeur de la version {version} dans {dist}.")
    return {
        "version": version,
        "published": published or date.today().isoformat(),
        "notes": release_notes(changelog, version),
        "assets": assets,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", help="tag publié (vX.Y.Z), vérifié contre __version__")
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    version = package_version(args.root)
    if args.tag and args.tag.removeprefix("v") != version:
        print(
            f"Le tag {args.tag} ne correspond pas à la version du paquet ({version}).",
            file=sys.stderr,
        )
        return 1
    manifest = build_manifest(args.dist, version, args.changelog)
    output = args.dist / "latest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{output} : version {version}, fichiers {', '.join(manifest['assets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
