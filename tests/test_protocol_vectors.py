"""Les vecteurs partagés avec l'application Android sont à jour et cohérents."""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "make_protocol_vectors", ROOT / "scripts" / "make_protocol_vectors.py"
)
vectors = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vectors)


def test_vectors_file_is_up_to_date():
    current = vectors.OUTPUT.read_text(encoding="utf-8")
    assert current == vectors.render(), (
        "Vecteurs de protocole obsolètes : lancez python scripts/make_protocol_vectors.py"
    )
