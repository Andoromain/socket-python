import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("make_latest", ROOT / "scripts" / "make_latest.py")
make_latest = importlib.util.module_from_spec(spec)
spec.loader.exec_module(make_latest)

from socket_chat import __version__  # noqa: E402
from socket_chat.updater import parse_manifest  # noqa: E402

CHANGELOG = """# Journal

## 9.9.9

### Ajouté
- Une nouveauté.

## 9.9.8

- Ancienne.
"""


def write_assets(dist: Path, version: str, *, windows=True, linux=True, android=False) -> None:
    dist.mkdir(parents=True, exist_ok=True)
    if windows:
        (dist / f"socket-chat-{version}-windows-setup.exe").write_bytes(b"exe" * 100)
    if linux:
        (dist / f"socket-chat-{version}-linux-x86_64.tar.gz").write_bytes(b"tgz" * 50)
    if android:
        (dist / f"socket-chat-{version}-android.apk").write_bytes(b"apk" * 70)


def test_manifest_lists_assets_and_notes(tmp_path):
    write_assets(tmp_path, "9.9.9")
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    manifest = make_latest.build_manifest(tmp_path, "9.9.9", changelog, "2026-11-05")
    assert manifest["notes"] == "### Ajouté\n- Une nouveauté."
    assert set(manifest["assets"]) == {"windows-x64", "linux-x86_64"}
    assert manifest["assets"]["windows-x64"]["size"] == 300
    # Le manifeste produit est accepté par l'application.
    parsed = parse_manifest(json.dumps(manifest).encode(), "https://exemple.fr/maj/latest.json")
    assert parsed.assets["linux-x86_64"].url == (
        "https://exemple.fr/maj/socket-chat-9.9.9-linux-x86_64.tar.gz"
    )


def test_android_apk_is_listed_and_ignored_by_desktop(tmp_path):
    write_assets(tmp_path, "9.9.9", android=True)
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    manifest = make_latest.build_manifest(tmp_path, "9.9.9", changelog)
    assert manifest["assets"]["android"]["name"] == "socket-chat-9.9.9-android.apk"
    assert manifest["assets"]["android"]["size"] == 210
    parsed = parse_manifest(json.dumps(manifest).encode(), "https://exemple.fr/maj/latest.json")
    # Un poste Windows ou Linux ne choisit jamais l'APK.
    for key in ("windows-x64", "linux-x86_64"):
        assert parsed.asset_for(key).name.endswith((".exe", ".tar.gz"))


def test_partial_platforms_and_missing_assets(tmp_path):
    changelog = tmp_path / "CHANGELOG.md"
    changelog.write_text(CHANGELOG, encoding="utf-8")
    write_assets(tmp_path / "a", "9.9.9", windows=False)
    assert set(make_latest.build_manifest(tmp_path / "a", "9.9.9", changelog)["assets"]) == {
        "linux-x86_64"
    }
    with pytest.raises(SystemExit):
        make_latest.build_manifest(tmp_path / "vide", "9.9.9", changelog)


def test_main_checks_tag_against_package_version(tmp_path, capsys):
    write_assets(tmp_path, __version__)
    assert make_latest.main(["--dist", str(tmp_path), "--tag", "v0.0.1"]) == 1
    assert "ne correspond pas" in capsys.readouterr().err
    assert make_latest.main(["--dist", str(tmp_path), "--tag", f"v{__version__}"]) == 0
    written = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
    assert written["version"] == __version__
    # Les notes de la version courante viennent du vrai CHANGELOG.
    assert written["notes"]


def test_check_tag_only(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    assert make_latest.main(["--check-tag", f"v{__version__}"]) == 0
    assert "conforme" in capsys.readouterr().out
    assert make_latest.main(["--check-tag", "v99.0.0"]) == 1
    captured = capsys.readouterr()
    assert "ne correspond pas à la version du code" in captured.err
    assert "git push origin --delete v99.0.0" in captured.err
    assert "::error" not in captured.out


def test_mismatch_is_annotated_in_github_actions(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    assert make_latest.main(["--check-tag", "v99.0.0"]) == 1
    assert captured_line_starts(capsys.readouterr().out, "::error title=Version incohérente::")


def captured_line_starts(output: str, prefix: str) -> bool:
    return any(line.startswith(prefix) for line in output.splitlines())
