import hashlib
import json
import tarfile
from pathlib import Path

import pytest
from PySide6.QtCore import QUrl
from update_keys import sign_manifest

from socket_chat import updater
from socket_chat.updater import (
    Asset,
    ManifestError,
    UpdateChecker,
    UpdateDownloader,
    asset_base,
    is_newer,
    normalize_source,
    parse_manifest,
    parse_version,
)

TIMEOUT = 10_000
URL = "https://github.com/o/r/releases/latest/download/latest.json"


def manifest_bytes(version="9.9.9", **asset_overrides):
    asset = {"name": "socket-chat-9.9.9-linux-x86_64.tar.gz", "size": 10, "sha256": "a" * 64}
    asset.update(asset_overrides)
    return json.dumps(
        {
            "version": version,
            "published": "2026-11-05",
            "notes": "- nouveau",
            "assets": {"linux-x86_64": asset},
        }
    ).encode()


# -- Fonctions pures -------------------------------------------------------------
def test_versions():
    assert parse_version("3.10.2") == (3, 10, 2)
    assert is_newer("3.10.0", "3.9.9")
    assert not is_newer("3.1.0", "3.1.0")
    assert not is_newer("3.0.9", "3.1.0")
    for bad in ("3.1", "v3.1.0", "3.1.0-beta", ""):
        with pytest.raises(ValueError):
            parse_version(bad)


def test_github_assets_target_the_exact_version():
    manifest = parse_manifest(manifest_bytes(), URL)
    assert manifest.assets["linux-x86_64"].url == (
        "https://github.com/o/r/releases/download/v9.9.9/socket-chat-9.9.9-linux-x86_64.tar.gz"
    )
    assert (
        asset_base("https://intranet/maj/latest.json", "1.0.0")
        == "https://intranet/maj/latest.json"
    )


@pytest.mark.parametrize(
    "data",
    [
        b"pas du json",
        b"[]",
        manifest_bytes(version="3.1"),
        manifest_bytes(name="../../evil.exe"),
        manifest_bytes(name="dossier/evil.exe"),
        manifest_bytes(size=0),
        manifest_bytes(size="10"),
        manifest_bytes(sha256="zz"),
        json.dumps({"version": "9.9.9", "assets": {}}).encode(),
        b"{" + b" " * (300 * 1024) + b"}",
    ],
)
def test_invalid_manifests_are_rejected(data):
    with pytest.raises(ManifestError):
        parse_manifest(data, URL)


def test_normalize_source(tmp_path):
    assert normalize_source(URL) == URL
    assert normalize_source("https://intranet/maj/") == "https://intranet/maj/latest.json"
    folder = normalize_source(str(tmp_path))
    assert folder.startswith("file://") and folder.endswith("/latest.json")
    assert Path(QUrl(folder).toLocalFile()) == tmp_path / "latest.json"


def test_plain_http_is_refused(qtbot):
    checker = UpdateChecker("http://intranet/maj/latest.json", "1.0.0")
    with qtbot.waitSignal(checker.failed, timeout=TIMEOUT) as blocker:
        checker.check_now(manual=True)
    assert "HTTPS" in blocker.args[0]


# -- Faux dépôt local (file://) ------------------------------------------------------
def make_repo(tmp_path: Path, version: str, payload: bytes, *, tamper: bool = False) -> Path:
    repo = tmp_path / "depot"
    repo.mkdir(exist_ok=True)
    name = f"socket-chat-{version}-linux-x86_64.tar.gz"
    (repo / name).write_bytes(payload)
    digest = hashlib.sha256(payload).hexdigest()
    if tamper:
        digest = "0" * 64
    manifest = {
        "version": version,
        "published": "2026-11-05",
        "notes": "### Ajouté\n- Mise à jour automatique",
        "assets": {"linux-x86_64": {"name": name, "size": len(payload), "sha256": digest}},
    }
    (repo / "latest.json").write_text(json.dumps(manifest), encoding="utf-8")
    sign_manifest(repo / "latest.json")
    return repo


def test_checker_detects_update(qtbot, tmp_path):
    repo = make_repo(tmp_path, "9.9.9", b"contenu")
    checker = UpdateChecker(str(repo), "3.2.0")
    with qtbot.waitSignal(checker.update_available, timeout=TIMEOUT) as blocker:
        checker.check_now()
    manifest = blocker.args[0]
    assert manifest.version == "9.9.9" and checker.latest is manifest
    assert "Mise à jour automatique" in manifest.notes


def test_checker_up_to_date_and_missing_source(qtbot, tmp_path):
    repo = make_repo(tmp_path, "1.0.0", b"x")
    checker = UpdateChecker(str(repo), "3.2.0")
    with qtbot.waitSignal(checker.up_to_date, timeout=TIMEOUT):
        checker.check_now(manual=True)
    assert checker.manual
    absent = UpdateChecker(str(tmp_path / "absent"), "3.2.0")
    with qtbot.waitSignal(absent.failed, timeout=TIMEOUT):
        absent.check_now()


def test_downloader_verifies_and_reports_progress(qtbot, tmp_path):
    payload = bytes(range(256)) * 4000
    repo = make_repo(tmp_path, "9.9.9", payload)
    manifest = parse_manifest(
        (repo / "latest.json").read_bytes(),
        QUrl.fromLocalFile(str(repo / "latest.json")).toString(),
    )
    downloader = UpdateDownloader()
    progress = []
    downloader.progress.connect(lambda done, total: progress.append((done, total)))
    with qtbot.waitSignal(downloader.finished, timeout=TIMEOUT) as blocker:
        downloader.start(manifest.assets["linux-x86_64"], tmp_path / "telechargements")
    path = Path(blocker.args[0])
    assert path.read_bytes() == payload
    assert progress[-1] == (len(payload), len(payload))
    assert not list(path.parent.glob("*.part"))


def test_downloader_rejects_corrupted_file(qtbot, tmp_path):
    repo = make_repo(tmp_path, "9.9.9", b"contenu", tamper=True)
    manifest = parse_manifest(
        (repo / "latest.json").read_bytes(),
        QUrl.fromLocalFile(str(repo / "latest.json")).toString(),
    )
    downloader = UpdateDownloader()
    with qtbot.waitSignal(downloader.failed, timeout=TIMEOUT) as blocker:
        downloader.start(manifest.assets["linux-x86_64"], tmp_path / "dl")
    assert "corrompu" in blocker.args[0]
    assert not any((tmp_path / "dl").iterdir())


def test_downloader_rejects_size_mismatch(qtbot, tmp_path):
    (tmp_path / "f.bin").write_bytes(b"0123456789")
    asset = Asset(
        "f.bin",
        4,
        hashlib.sha256(b"0123").hexdigest(),
        QUrl.fromLocalFile(str(tmp_path / "f.bin")).toString(),
    )
    downloader = UpdateDownloader()
    with qtbot.waitSignal(downloader.failed, timeout=TIMEOUT):
        downloader.start(asset, tmp_path / "dl")


# -- Installation ---------------------------------------------------------------------
def test_extract_archive_finds_install_script(tmp_path):
    bundle = tmp_path / "socket-chat-9.9.9-linux-x86_64"
    bundle.mkdir()
    (bundle / "install.sh").write_text("#!/bin/sh\n")
    archive = tmp_path / "dl" / "maj.tar.gz"
    archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(bundle, arcname=bundle.name)
    script = updater._extract_archive(archive)
    assert script.name == "install.sh" and script.parent.name == bundle.name


def test_extract_archive_refuses_path_traversal(tmp_path):
    evil = tmp_path / "evil.txt"
    evil.write_text("x")
    archive = tmp_path / "dl" / "maj.tar.gz"
    archive.parent.mkdir()
    with tarfile.open(archive, "w:gz") as tar:
        tar.add(evil, arcname="../../evil.txt")
    with pytest.raises(tarfile.TarError):
        updater._extract_archive(archive)


def test_cleanup_downloads(tmp_path):
    old = tmp_path / f"{updater.DOWNLOAD_PREFIX}abc"
    old.mkdir()
    (old / "installeur.exe").write_bytes(b"x")
    unrelated = tmp_path / "autre-chose"
    unrelated.mkdir()
    assert updater.cleanup_downloads(tmp_path) == 1
    assert not old.exists() and unrelated.exists()


# -- Signature du manifeste ----------------------------------------------------------
def test_signature_url_targets_the_exact_version():
    assert updater.signature_url(URL, "9.9.9") == (
        "https://github.com/o/r/releases/download/v9.9.9/latest.json.sig"
    )
    assert (
        updater.signature_url("https://intranet/maj/canal%20stable.json", "1.0.0")
        == "https://intranet/maj/canal%20stable.json.sig"
    )
    assert updater.signature_url("file:///srv/maj/latest.json", "1.0.0") == (
        "file:///srv/maj/latest.json.sig"
    )


def test_signature_verification():
    from update_keys import PRIVATE_KEY

    from socket_chat import signing

    data = manifest_bytes()
    signature = signing.sign(data, PRIVATE_KEY)
    signing.verify(data, signature)
    signing.verify(data, (signature + "\n").encode())  # fichier .sig avec retour à la ligne
    other = signing.generate_private_key()
    bad_cases = [
        (data + b" ", signature),  # manifeste modifié
        (data, signing.sign(data, other)),  # autre clé
        (data, "pas du base64 !"),
        (data, "AAAA"),  # longueur invalide
        (data, "A" * 2000),  # trop gros
        (data, ""),
    ]
    for payload, sig in bad_cases:
        with pytest.raises(signing.SignatureError):
            signing.verify(payload, sig)
    # Rotation : une seconde clé de confiance est acceptée.
    signing.verify(data, signing.sign(data, other), (signing.public_key_of(other),))


@pytest.mark.parametrize("case", ["absente", "modifiee", "autre_cle", "illisible"])
def test_checker_refuses_unsigned_or_forged_manifest(qtbot, tmp_path, case):
    from socket_chat import signing

    repo = make_repo(tmp_path, "9.9.9", b"contenu")
    manifest = repo / "latest.json"
    sig = repo / "latest.json.sig"
    if case == "absente":
        sig.unlink()
    elif case == "modifiee":
        # Un attaquant remplace l'empreinte de l'installeur par celle du sien.
        payload = json.loads(manifest.read_text())
        payload["assets"]["linux-x86_64"]["sha256"] = "0" * 64
        manifest.write_text(json.dumps(payload))
    elif case == "autre_cle":
        sign_manifest(manifest, signing.generate_private_key())
    else:
        sig.write_text("n'importe quoi")
    checker = UpdateChecker(str(repo), "3.2.0")
    available = []
    checker.update_available.connect(available.append)
    with qtbot.waitSignal(checker.failed, timeout=TIMEOUT) as blocker:
        checker.check_now()
    assert not available and checker.latest is None
    if case != "absente":
        assert "signature invalide" in blocker.args[0]


def test_sign_manifest_script(tmp_path, monkeypatch, capsys):
    import importlib.util

    from update_keys import PRIVATE_KEY

    spec = importlib.util.spec_from_file_location(
        "sign_manifest", Path(__file__).parents[1] / "scripts" / "sign_manifest.py"
    )
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)

    manifest = tmp_path / "latest.json"
    manifest.write_bytes(manifest_bytes())
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    monkeypatch.delenv("UPDATE_SIGNING_KEY", raising=False)
    assert script.main([str(manifest)]) == 1  # clé absente

    from socket_chat import signing

    monkeypatch.setenv("UPDATE_SIGNING_KEY", signing.generate_private_key())
    assert script.main([str(manifest)]) == 1  # clé qui ne correspond pas à l'application
    assert not (tmp_path / "latest.json.sig").exists()

    monkeypatch.setenv("UPDATE_SIGNING_KEY", PRIVATE_KEY)
    assert script.main([str(manifest)]) == 0
    signing.verify(manifest.read_bytes(), (tmp_path / "latest.json.sig").read_text())

    assert script.main(["--generate"]) == 0
    assert "Clé publique" in capsys.readouterr().out


def test_embedded_public_key_is_valid():
    """La clé publique livrée avec l'application est bien une clé Ed25519."""
    import ast

    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    from socket_chat import signing

    tree = ast.parse(Path(signing.__file__).read_text(encoding="utf-8"))
    (keys,) = [
        ast.literal_eval(node.value)
        for node in tree.body
        if isinstance(node, ast.AnnAssign) and getattr(node.target, "id", "") == "TRUSTED_KEYS"
    ]
    assert keys
    for key in keys:
        Ed25519PublicKey.from_public_bytes(signing._decode(key, 32, "Clé publique"))
