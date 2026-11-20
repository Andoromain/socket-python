import ipaddress

from PySide6.QtCore import QSettings

from socket_chat.netpolicy import is_allowed, parse_networks
from socket_chat.settings import Settings


def test_parse_networks_accepts_cidr_addresses_and_separators():
    networks = parse_networks(" 10.20.0.0/16 ; 192.168.1.7, fd00::/8,, pas-un-réseau, 10.1.2.3/8")
    assert networks == [
        ipaddress.ip_network("10.20.0.0/16"),
        ipaddress.ip_network("192.168.1.7/32"),
        ipaddress.ip_network("fd00::/8"),
        ipaddress.ip_network("10.0.0.0/8"),  # bits d'hôte tolérés
    ]
    assert parse_networks(["10.0.0.0/8", " "]) == [ipaddress.ip_network("10.0.0.0/8")]


def test_is_allowed():
    networks = parse_networks("10.20.0.0/16, fd00::/8")
    assert is_allowed("10.20.3.4", networks)
    assert is_allowed("::ffff:10.20.3.4", networks)  # socket double pile
    assert is_allowed("fd12::1", networks)
    assert not is_allowed("10.21.0.1", networks)
    assert not is_allowed("192.168.1.10", networks)
    assert not is_allowed("::ffff:192.168.1.10", networks)
    assert not is_allowed("2001:db8::1", networks)
    assert not is_allowed("fe80::1%eth0", networks)
    assert not is_allowed("", networks) and not is_allowed("nom-de-poste", networks)
    # Boucle locale toujours permise ; liste vide : aucune restriction.
    assert is_allowed("127.0.0.1", networks) and is_allowed("::1", networks)
    assert is_allowed("8.8.8.8", [])


def test_allowed_networks_setting(tmp_path):
    store = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    assert settings.allowed_networks == []
    store.setValue("network/allowed_networks", "10.0.0.0/8, 192.168.1.0/24")
    assert [str(n) for n in settings.allowed_networks] == ["10.0.0.0/8", "192.168.1.0/24"]
    store.setValue("network/allowed_networks", ["172.16.0.0/12", "fd00::/8"])
    assert [str(n) for n in settings.allowed_networks] == ["172.16.0.0/12", "fd00::/8"]
