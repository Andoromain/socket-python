"""Génère ``latest.json`` (manifeste de mise à jour) à partir des fichiers construits.

Usage : python scripts/make_latest.py --dist dist [--tag v3.2.0]
        python scripts/make_latest.py --check-tag v3.2.0   (vérification seule)

Le manifeste liste, pour chaque plate-forme trouvée, le nom, la taille et l'empreinte
SHA-256 de l'installeur, ainsi que les notes de la version tirées de CHANGELOG.md.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
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


def tag_error(tag: str, version: str) -> str | None:
    """Message d'erreur si le tag ne désigne pas la version du code, sinon ``None``."""
    if tag.removeprefix("v") == version:
        return None
    return (
        f"Le tag {tag} ne correspond pas à la version du code ({version}). "
        f"Publier ce code sous un autre numéro ferait proposer la mise à jour en boucle. "
        f"Pour publier {tag.removeprefix('v')} : changez d'abord la version "
        "(src/socket_chat/__init__.py, packaging/windows/version_info.txt, CHANGELOG.md), "
        f"fusionnez, puis créez le tag. Supprimez ce tag : git push origin --delete {tag}"
    )


def report_error(message: str) -> None:
    print(message, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        # Annotation visible directement sur la page de l'exécution GitHub Actions.
        print(f"::error title=Version incohérente::{message}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dist", type=Path, default=ROOT / "dist")
    parser.add_argument("--tag", help="tag publié (vX.Y.Z), vérifié contre __version__")
    parser.add_argument(
        "--check-tag", metavar="TAG", help="vérifier seulement que TAG correspond à la version"
    )
    parser.add_argument("--changelog", type=Path, default=ROOT / "CHANGELOG.md")
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    version = package_version(args.root)
    tag = args.check_tag or args.tag
    if tag and (error := tag_error(tag, version)):
        report_error(error)
        return 1
    if args.check_tag:
        print(f"Tag {tag} conforme à la version du code ({version}).")
        return 0
    manifest = build_manifest(args.dist, version, args.changelog)
    output = args.dist / "latest.json"
    output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"{output} : version {version}, fichiers {', '.join(manifest['assets'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
