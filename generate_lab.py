#!/usr/bin/env python3
"""
generate_lab.py

Generatore minimale di laboratori Kathara a partire da file YAML.

Output:
- lab.conf nel formato Kathara classico;
- <device>.startup per ogni dispositivo;
- per router/switch/firewall:
    <device>/etc/frr/daemons
    <device>/etc/frr/frr.conf
    <device>/etc/frr/vtysh.conf

Formato .startup dei router/switch/firewall:
    ip address add 172.16.1.1/24 dev eth0

    ip route add default via 172.16.1.2

    systemctl start frr

Quindi:
- niente shebang;
- niente set -e;
- niente echo;
- niente if;
- niente vtysh automatico;
- avvio FRR con systemctl start frr.

Uso:
    python3 generate_lab.py configs/extended-mesh-lans.yml --clean
    python3 generate_lab.py extended-mesh-lans --config-dir configs --output-dir labs --clean

Dipendenza:
    pip install pyyaml
"""

import argparse
import ipaddress
import os
import shutil
import sys
from pathlib import Path
from typing import Any

try:
    import yaml
except ImportError:
    print("Errore: PyYAML non installato. Esegui: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


FRR_DEVICE_TYPES = {"router", "switch", "firewall"}
BASE_IMAGE = "kathara/base"
FRR_IMAGE = "kathara/frr"


# ---------------------------------------------------------------------------
# Lettura e validazione YAML
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File YAML non trovato: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Il file {path} non contiene una configurazione YAML valida.")

    return data


def resolve_config_path(argument: str, config_dir: Path) -> Path:
    candidate = Path(argument)

    if candidate.exists():
        return candidate

    for extension in (".yml", ".yaml"):
        possible = config_dir / f"{argument}{extension}"
        if possible.exists():
            return possible

    raise FileNotFoundError(
        f"Configurazione non trovata per '{argument}'. "
        f"Passa un file YAML valido oppure metti {argument}.yml in {config_dir}"
    )


def validate_config(config: dict[str, Any]) -> None:
    if "lab_name" not in config:
        raise ValueError("Campo obbligatorio mancante: lab_name")

    if "nodes" not in config:
        raise ValueError("Campo obbligatorio mancante: nodes")

    if not isinstance(config["nodes"], dict) or not config["nodes"]:
        raise ValueError("Il campo nodes deve essere un dizionario non vuoto.")

    for node_name, node_data in config["nodes"].items():
        if not isinstance(node_data, dict):
            raise ValueError(f"Il nodo {node_name} deve essere un dizionario.")

        interfaces = node_data.get("interfaces")
        if not isinstance(interfaces, list) or not interfaces:
            raise ValueError(f"Il nodo {node_name} deve avere almeno una interfaccia.")

        for index, interface in enumerate(interfaces):
            if not isinstance(interface, dict):
                raise ValueError(f"Il nodo {node_name}, interfaccia {index}, non è valida.")

            if "network" not in interface:
                raise ValueError(
                    f"Il nodo {node_name}, interfaccia {index}, non ha il campo network."
                )


def prepare_lab_directory(lab_dir: Path, clean: bool, force: bool) -> None:
    if lab_dir.exists() and clean:
        shutil.rmtree(lab_dir)

    if lab_dir.exists() and not force and not clean:
        raise FileExistsError(
            f"La cartella {lab_dir} esiste già. "
            "Usa --force per sovrascrivere oppure --clean per eliminarla e rigenerarla."
        )

    lab_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def node_type(node_data: dict[str, Any]) -> str:
    return str(node_data.get("type", "host")).lower()


def is_frr_device(node_data: dict[str, Any]) -> bool:
    if node_type(node_data) in FRR_DEVICE_TYPES:
        return True

    frr = node_data.get("frr")
    if not frr:
        return False

    if isinstance(frr, dict) and frr.get("enabled", True) is False:
        return False

    return True


def get_node_image(node_data: dict[str, Any]) -> str:
    image = node_data.get("image")

    if image:
        return str(image)

    if is_frr_device(node_data):
        return FRR_IMAGE

    return BASE_IMAGE


def get_network_from_ip(ip_with_prefix: str) -> str:
    return str(ipaddress.ip_interface(ip_with_prefix).network)


def get_frr_section(node_data: dict[str, Any]) -> dict[str, Any]:
    frr = node_data.get("frr", {})
    if frr is None:
        return {}
    if not isinstance(frr, dict):
        raise ValueError("La sezione frr deve essere un dizionario.")
    return frr


def get_frr_protocols(node_data: dict[str, Any]) -> list[str]:
    frr = get_frr_section(node_data)
    protocols: list[str] = []

    if "protocol" in frr:
        protocols.append(str(frr["protocol"]).lower())

    if "protocols" in frr:
        for proto in frr["protocols"]:
            protocols.append(str(proto).lower())

    daemons = frr.get("daemons", {})
    if isinstance(daemons, dict):
        if daemons.get("ospfd"):
            protocols.append("ospf")
        if daemons.get("ripd"):
            protocols.append("rip")
        if daemons.get("bgpd"):
            protocols.append("bgp")

    result = []
    for proto in protocols:
        if proto not in result:
            result.append(proto)

    return result


# ---------------------------------------------------------------------------
# lab.conf
# ---------------------------------------------------------------------------

def generate_lab_conf(config: dict[str, Any]) -> str:
    lab_name = config["lab_name"]
    nodes = config["nodes"]
    metadata = config.get("metadata", {})

    description = metadata.get("description", config.get("description", lab_name))
    version = metadata.get("version", "2.0")
    author = metadata.get("author", "Luca-Bortoluzzi")
    email = metadata.get("email", "luca.bortoluzzi921@edu.unito.it")
    web = metadata.get("web", "http://www.kathara.org/")

    lines = [
        f'LAB_DESCRIPTION="{description}"',
        f"LAB_VERSION={version}",
        f'LAB_AUTHOR="{author}"',
        f"LAB_EMAIL={email}",
        f"LAB_WEB={web}",
        "",
    ]

    for node_name, node_data in nodes.items():
        for index, interface in enumerate(node_data["interfaces"]):
            network = interface["network"]
            lines.append(f'{node_name}[{index}]="{network}"')

        lines.append(f'{node_name}[image]="{get_node_image(node_data)}"')
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# FRR
# ---------------------------------------------------------------------------

def get_frr_networks(node_data: dict[str, Any]) -> list[str]:
    frr = get_frr_section(node_data)
    networks = frr.get("networks")

    if networks:
        return [str(network) for network in networks]

    result = []
    for interface in node_data["interfaces"]:
        ip_addr = interface.get("ip")
        if ip_addr:
            result.append(get_network_from_ip(ip_addr))

    return result


def generate_frr_daemons(node_data: dict[str, Any]) -> str:
    protocols = get_frr_protocols(node_data)

    ospfd = "yes" if "ospf" in protocols else "no"
    ripd = "yes" if "rip" in protocols else "no"
    bgpd = "yes" if "bgp" in protocols else "no"

    return f"""# Auto-generated FRR daemons file
zebra=yes
bgpd={bgpd}
ospfd={ospfd}
ripd={ripd}
ospf6d=no
ripngd=no
isisd=no
pimd=no
ldpd=no
nhrpd=no
eigrpd=no
babeld=no
sharpd=no
pbrd=no
bfdd=no
fabricd=no

vtysh_enable=yes
zebra_options="  -A 127.0.0.1"
bgpd_options="   -A 127.0.0.1"
ospfd_options="  -A 127.0.0.1"
ripd_options="   -A 127.0.0.1"
"""


def generate_minimal_frr_conf(node_name: str) -> str:
    return "\n".join([
        "frr defaults traditional",
        f"hostname {node_name}",
        "service integrated-vtysh-config",
        "!",
        "line vty",
        "!",
        "",
    ])


def generate_frr_conf(node_name: str, node_data: dict[str, Any]) -> str:
    frr = get_frr_section(node_data)

    if "config" in frr:
        return str(frr["config"]).rstrip() + "\n"

    protocols = get_frr_protocols(node_data)
    if not protocols:
        return generate_minimal_frr_conf(node_name)

    lines = [
        "frr defaults traditional",
        f"hostname {node_name}",
        "service integrated-vtysh-config",
        "!",
    ]

    if "ospf" in protocols:
        router_id = frr.get("router_id")
        area = frr.get("area", 0)

        lines.append("router ospf")
        if router_id:
            lines.append(f" ospf router-id {router_id}")

        for network in get_frr_networks(node_data):
            lines.append(f" network {network} area {area}")

        lines.append("!")

    if "rip" in protocols:
        lines.append("router rip")
        lines.append(" version 2")
        lines.append(" no auto-summary")

        for network in get_frr_networks(node_data):
            lines.append(f" network {network}")

        lines.append("!")

    if "bgp" in protocols:
        if "asn" not in frr:
            raise ValueError(f"Il nodo {node_name} usa BGP ma manca frr.asn")

        lines.append(f"router bgp {frr['asn']}")

        if frr.get("router_id"):
            lines.append(f" bgp router-id {frr['router_id']}")

        for neighbor in frr.get("neighbors", []):
            lines.append(f" neighbor {neighbor['ip']} remote-as {neighbor['remote_as']}")

        for network in frr.get("networks", []):
            lines.append(f" network {network}")

        lines.append("!")

    lines.extend([
        "line vty",
        "!",
        "",
    ])

    return "\n".join(lines)


def write_frr_files(lab_dir: Path, node_name: str, node_data: dict[str, Any]) -> None:
    frr_dir = lab_dir / node_name / "etc" / "frr"
    frr_dir.mkdir(parents=True, exist_ok=True)

    (frr_dir / "daemons").write_text(generate_frr_daemons(node_data), encoding="utf-8")
    (frr_dir / "frr.conf").write_text(generate_frr_conf(node_name, node_data), encoding="utf-8")
    (frr_dir / "vtysh.conf").write_text(
        "service integrated-vtysh-config\n",
        encoding="utf-8"
    )


# ---------------------------------------------------------------------------
# Startup Kathara nel formato richiesto
# ---------------------------------------------------------------------------

def generate_startup(node_name: str, node_data: dict[str, Any]) -> str:
    """
    Genera un file .startup con il formato richiesto:

        ip address add 172.16.1.1/24 dev eth0

        ip route add default via 172.16.1.2

        systemctl start frr

    Non vengono generati script bash.
    """
    lines: list[str] = []

    # Indirizzi IP sulle interfacce.
    for index, interface in enumerate(node_data["interfaces"]):
        ip_addr = interface.get("ip")
        if ip_addr:
            lines.append(f"ip address add {ip_addr} dev eth{index}")

    # Riga vuota tra indirizzi e routing, se serve.
    if lines and (node_data.get("default_gateway") or node_data.get("routes") or is_frr_device(node_data)):
        lines.append("")

    # Default gateway.
    if node_data.get("default_gateway"):
        lines.append(f"ip route add default via {node_data['default_gateway']}")

    # Rotte statiche opzionali.
    if node_data.get("routes"):
        for route in node_data["routes"]:
            lines.append(f"ip route add {route['to']} via {route['via']}")

    # Riga vuota tra routing e avvio FRR.
    if is_frr_device(node_data):
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("systemctl start frr")

    # Comandi custom opzionali.
    if node_data.get("commands"):
        if lines and lines[-1] != "":
            lines.append("")
        for command in node_data["commands"]:
            lines.append(str(command))

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# Generazione laboratorio
# ---------------------------------------------------------------------------

def generate_lab(config_path: Path, output_dir: Path, clean: bool, force: bool) -> Path:
    config = load_yaml(config_path)
    validate_config(config)

    lab_name = str(config["lab_name"])
    nodes = config["nodes"]

    lab_dir = output_dir / lab_name
    prepare_lab_directory(lab_dir, clean=clean, force=force)

    (lab_dir / "lab.conf").write_text(generate_lab_conf(config), encoding="utf-8")

    for node_name, node_data in nodes.items():
        startup_path = lab_dir / f"{node_name}.startup"
        startup_path.write_text(generate_startup(node_name, node_data), encoding="utf-8")
        os.chmod(startup_path, 0o644)

        if is_frr_device(node_data):
            write_frr_files(lab_dir, node_name, node_data)

    return lab_dir


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera lab.conf, file .startup e configurazioni FRR per Kathara."
    )

    parser.add_argument(
        "lab",
        help="File YAML da leggere oppure nome del laboratorio.",
    )

    parser.add_argument(
        "--config-dir",
        default="configs",
        type=Path,
        help="Cartella dove cercare i file YAML se viene passato solo il nome del lab. Default: configs",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        default="labs",
        type=Path,
        help="Cartella di output dei laboratori generati. Default: labs",
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help="Elimina e ricrea la cartella del laboratorio se esiste già.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Sovrascrive i file se la cartella del laboratorio esiste già.",
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config_path = resolve_config_path(args.lab, args.config_dir)
        lab_dir = generate_lab(
            config_path=config_path,
            output_dir=args.output_dir,
            clean=args.clean,
            force=args.force,
        )

        print(f"[OK] Configurazione letta: {config_path}")
        print(f"[OK] Laboratorio generato: {lab_dir}")
        print("")
        print("Per avviare:")
        print(f"  cd {lab_dir}")
        print("  kathara lstart")
        print("")
        print("Per pulire:")
        print("  kathara lclean")

        return 0

    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
