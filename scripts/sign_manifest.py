"""Signe ``latest.json`` (Ed25519) pour la mise à jour automatique.

Usage : python scripts/sign_manifest.py dist/latest.json   (clé dans UPDATE_SIGNING_KEY)
        python scripts/sign_manifest.py --generate         (nouvelle paire de clés)

Écrit ``latest.json.sig`` à côté du manifeste, puis vérifie la signature avec les clés
publiques intégrées à l'application (``socket_chat.signing.TRUSTED_KEYS``) : une clé
privée qui ne correspond pas à l'application est refusée avant toute publication.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from socket_chat import signing  # noqa: E402

KEY_VARIABLE = "UPDATE_SIGNING_KEY"


def report_error(title: str, message: str) -> None:
    print(message, file=sys.stderr)
    if os.environ.get("GITHUB_ACTIONS") == "true":
        print(f"::error title={title}::{message}")


def sign_file(manifest: Path, private_key: str) -> Path:
    data = manifest.read_bytes()
    signature = signing.sign(data, private_key)
    signing.verify(data, signature)  # SignatureError si la clé n'est pas celle de l'app
    output = manifest.with_name(manifest.name + signing.SIGNATURE_SUFFIX)
    output.write_text(signature + "\n", encoding="ascii")
    return output


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("manifest", nargs="?", type=Path, help="fichier latest.json à signer")
    parser.add_argument("--generate", action="store_true", help="créer une paire de clés")
    args = parser.parse_args(argv)

    if args.generate:
        private_key = signing.generate_private_key()
        print(f"Clé privée (secret {KEY_VARIABLE}, à garder secrète) :\n{private_key}")
        print(f"Clé publique (signing.TRUSTED_KEYS) :\n{signing.public_key_of(private_key)}")
        return 0
    if args.manifest is None:
        parser.error("indiquez le manifeste à signer, ou --generate")

    private_key = os.environ.get(KEY_VARIABLE, "").strip()
    if not private_key:
        report_error("Clé de signature absente", f"Variable {KEY_VARIABLE} vide.")
        return 1
    try:
        output = sign_file(args.manifest, private_key)
    except signing.SignatureError as exc:
        report_error(
            "Clé de signature refusée",
            f"Secret {KEY_VARIABLE} incorrect ({exc}) : il ne correspond pas à la clé "
            "publique intégrée à l'application (src/socket_chat/signing.py).",
        )
        return 1
    print(f"{output} : signature vérifiée.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
