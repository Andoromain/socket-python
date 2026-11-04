import os

# Tests sans affichage (CI, serveurs).
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
