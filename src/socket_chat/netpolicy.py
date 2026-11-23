"""Réseaux autorisés : limite les postes avec lesquels Socket Chat accepte de parler.

L'administrateur fixe la clé ``network/allowed_networks`` (par exemple
``10.20.0.0/16, 192.168.1.0/24``). Une connexion ou une annonce venant d'une autre
adresse est ignorée : un portable branché sur un réseau extérieur (hôtel, domicile,
Wi-Fi public) ne parle plus qu'aux réseaux de l'entreprise. Liste vide : aucune
restriction (comportement par défaut). La boucle locale reste toujours autorisée.

Module sans Qt.
"""

from __future__ import annotations

import ipaddress
import logging
from collections.abc import Iterable

log = logging.getLogger(__name__)

Network = ipaddress.IPv4Network | ipaddress.IPv6Network


def parse_networks(entries: Iterable[str] | str) -> list[Network]:
    """Réseaux au format CIDR (``10.0.0.0/8``) ou adresses seules ; entrées invalides ignorées."""
    if isinstance(entries, str):
        entries = entries.replace(";", ",").split(",")
    networks: list[Network] = []
    for entry in entries:
        entry = str(entry).strip()
        if not entry:
            continue
        try:
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            log.warning("Réseau autorisé invalide ignoré : %r", entry)
    return networks


def is_allowed(address: str, networks: list[Network]) -> bool:
    """Vrai si ``address`` appartient à l'un des réseaux (ou si la liste est vide)."""
    if not networks:
        return True
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])  # « fe80::1%eth0 »
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped  # « ::ffff:10.0.0.5 » (sockets double pile)
    if ip.is_loopback:
        return True
    return any(ip.version == net.version and ip in net for net in networks)
