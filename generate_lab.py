#!/usr/bin/env python3
"""
generate_lab.py

Script unico per generare un laboratorio Kathara a partire da un file YAML.

Uso consigliato:
    python3 generate_lab.py configs/lan-dmz.yml
    python3 generate_lab.py configs/extended-mesh-lans.yml
    python3 generate_lab.py extended-mesh-lans --config-dir configs --output-dir labs

Lo script genera:
    labs/<lab_name>/
    ├── lab.conf
    ├── <nodo>.startup
    ├── <router>/etc/frr/daemons       se il nodo ha sezione frr
    ├── <router>/etc/frr/frr.conf      se il nodo ha sezione frr
    └── README.md
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


def load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"File YAML non trovato: {path}")

    with path.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Il file {path} non contiene una configurazione YAML valida.")

    return data


def resolve_config_path(argument: str, config_dir: Path) -> Path:
    """
    Permette due modalità:
    1. passare direttamente il file:
         python3 generate_lab.py configs/lan-dmz.yml

    2. passare solo il nome del lab:
         python3 generate_lab.py lan-dmz --config-dir configs
       In questo caso cerca:
         configs/lan-dmz.yml
         configs/lan-dmz.yaml
    """
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


def generate_lab_conf(nodes: dict[str, Any]) -> str:
    lines = [
        "# Auto-generated lab.conf",
        ""
    ]

    for node_name, node_data in nodes.items():
        for index, interface in enumerate(node_data["interfaces"]):
            network = interface["network"]
            lines.append(f'{node_name}[{index}]="{network}"')

    return "\n".join(lines) + "\n"


def get_network_from_ip(ip_with_prefix: str) -> str:
    """
    Esempio:
        10.0.12.1/30 -> 10.0.12.0/30
    """
    return str(ipaddress.ip_interface(ip_with_prefix).network)


def frr_enabled(node_data: dict[str, Any]) -> bool:
    frr = node_data.get("frr")
    if not frr:
        return False

    if isinstance(frr, dict) and frr.get("enabled", True) is False:
        return False

    return True


def get_frr_protocol(node_data: dict[str, Any]) -> str:
    frr = node_data.get("frr", {})
    return str(frr.get("protocol", "ospf")).lower()


def generate_frr_daemons(node_data: dict[str, Any]) -> str:
    protocol = get_frr_protocol(node_data)

    ospfd = "yes" if protocol == "ospf" else "no"
    bgpd = "yes" if protocol == "bgp" else "no"
    ripd = "yes" if protocol == "rip" else "no"

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


def generate_ospf_conf(node_name: str, node_data: dict[str, Any]) -> str:
    frr = node_data.get("frr", {})
    router_id = frr.get("router_id")
    area = frr.get("area", 0)

    networks = frr.get("networks")
    if not networks:
        networks = []
        for interface in node_data["interfaces"]:
            ip_addr = interface.get("ip")
            if ip_addr:
                networks.append(get_network_from_ip(ip_addr))

    lines = [
        "frr defaults traditional",
        f"hostname {node_name}",
        "service integrated-vtysh-config",
        "!",
        "router ospf"
    ]

    if router_id:
        lines.append(f" ospf router-id {router_id}")

    for network in networks:
        lines.append(f" network {network} area {area}")

    lines.extend([
        "!",
        "line vty",
        "!"
    ])

    return "\n".join(lines) + "\n"


def generate_bgp_conf(node_name: str, node_data: dict[str, Any]) -> str:
    frr = node_data.get("frr", {})

    if "asn" not in frr:
        raise ValueError(f"Il nodo {node_name} usa BGP ma manca frr.asn")

    asn = frr["asn"]
    router_id = frr.get("router_id")
    networks = frr.get("networks", [])
    neighbors = frr.get("neighbors", [])

    lines = [
        "frr defaults traditional",
        f"hostname {node_name}",
        "service integrated-vtysh-config",
        "!",
        f"router bgp {asn}"
    ]

    if router_id:
        lines.append(f" bgp router-id {router_id}")

    for neighbor in neighbors:
        lines.append(f" neighbor {neighbor['ip']} remote-as {neighbor['remote_as']}")

    for network in networks:
        lines.append(f" network {network}")

    lines.extend([
        "!",
        "line vty",
        "!"
    ])

    return "\n".join(lines) + "\n"


def generate_frr_conf(node_name: str, node_data: dict[str, Any]) -> str:
    """
    Se nello YAML è presente frr.config, viene usata direttamente.
    Altrimenti genera una configurazione automatica OSPF/BGP minima.
    """
    frr = node_data.get("frr", {})

    if "config" in frr:
        return str(frr["config"]).rstrip() + "\n"

    protocol = get_frr_protocol(node_data)

    if protocol == "ospf":
        return generate_ospf_conf(node_name, node_data)

    if protocol == "bgp":
        return generate_bgp_conf(node_name, node_data)

    raise ValueError(f"Protocollo FRR non supportato sul nodo {node_name}: {protocol}")


def generate_startup(node_name: str, node_data: dict[str, Any]) -> str:
    lines = [
        "#!/bin/bash",
        f"# Auto-generated startup file for node: {node_name}",
        "set -e",
        ""
    ]

    for index, interface in enumerate(node_data["interfaces"]):
        network = interface["network"]
        ip_addr = interface.get("ip")

        lines.append(f"# eth{index} -> {network}")
        lines.append(f"ip link set eth{index} up")

        if ip_addr:
            lines.append(f"ip addr add {ip_addr} dev eth{index}")

        lines.append("")

    node_type = str(node_data.get("type", "host")).lower()

    if node_type in ("router", "firewall") or frr_enabled(node_data):
        lines.append("# Enable IPv4 forwarding")
        lines.append("sysctl -w net.ipv4.ip_forward=1")
        lines.append("")

    if node_data.get("default_gateway"):
        lines.append("# Default gateway")
        lines.append(f"ip route add default via {node_data['default_gateway']}")
        lines.append("")

    if node_data.get("routes"):
        lines.append("# Static routes")
        for route in node_data["routes"]:
            lines.append(f"ip route add {route['to']} via {route['via']}")
        lines.append("")

    if frr_enabled(node_data):
        lines.append("# Start FRR")
        lines.append("chown -R frr:frr /etc/frr || true")
        lines.append("chmod 640 /etc/frr/daemons || true")
        lines.append("chmod 640 /etc/frr/frr.conf || true")
        lines.append("service frr start || /usr/lib/frr/frrinit.sh start || true")
        lines.append("")

    if node_data.get("commands"):
        lines.append("# Custom commands")
        for command in node_data["commands"]:
            lines.append(str(command))
        lines.append("")

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


def generate_readme(config: dict[str, Any]) -> str:
    lab_name = config["lab_name"]
    description = config.get("description", "Laboratorio Kathara generato automaticamente.")
    nodes = config["nodes"]

    lines = [
        f"# {lab_name}",
        "",
        description,
        "",
        "## Nodi",
        ""
    ]

    for node_name, node_data in nodes.items():
        lines.append(f"### {node_name}")
        lines.append("")
        lines.append(f"- Tipo: `{node_data.get('type', 'host')}`")

        for index, interface in enumerate(node_data["interfaces"]):
            lines.append(
                f"- eth{index}: rete `{interface['network']}`, IP `{interface.get('ip', '-')}`"
            )

        if node_data.get("default_gateway"):
            lines.append(f"- Gateway: `{node_data['default_gateway']}`")

        if frr_enabled(node_data):
            lines.append(f"- FRR: `{get_frr_protocol(node_data)}`")

        lines.append("")

    lines.extend([
        "## Avvio",
        "",
        "```bash",
        "kathara lstart",
        "```",
        "",
        "## Pulizia",
        "",
        "```bash",
        "kathara lclean",
        "```",
        ""
    ])

    return "\n".join(lines)


def generate_lab(config_path: Path, output_dir: Path, clean: bool, force: bool) -> Path:
    config = load_yaml(config_path)
    validate_config(config)

    lab_name = config["lab_name"]
    nodes = config["nodes"]

    lab_dir = output_dir / lab_name
    prepare_lab_directory(lab_dir, clean=clean, force=force)

    (lab_dir / "lab.conf").write_text(generate_lab_conf(nodes), encoding="utf-8")

    for node_name, node_data in nodes.items():
        startup_path = lab_dir / f"{node_name}.startup"
        startup_path.write_text(generate_startup(node_name, node_data), encoding="utf-8")
        os.chmod(startup_path, 0o755)

        if frr_enabled(node_data):
            write_frr_files(lab_dir, node_name, node_data)

    (lab_dir / "README.md").write_text(generate_readme(config), encoding="utf-8")

    return lab_dir


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Genera i file Kathara necessari per il laboratorio selezionato."
    )

    parser.add_argument(
        "lab",
        help=(
            "File YAML da leggere oppure nome del laboratorio. "
            "Esempi: configs/lan-dmz.yml oppure lan-dmz"
        )
    )

    parser.add_argument(
        "--config-dir",
        default="configs",
        type=Path,
        help="Cartella dove cercare i file YAML se viene passato solo il nome del lab. Default: configs"
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        default="labs",
        type=Path,
        help="Cartella di output dei laboratori generati. Default: labs"
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help="Elimina e ricrea la cartella del laboratorio se esiste già."
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Sovrascrive i file se la cartella del laboratorio esiste già."
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
            force=args.force
        )

        print(f"[OK] Configurazione letta: {config_path}")
        print(f"[OK] Laboratorio generato: {lab_dir}")
        print("")
        print("Avvio:")
        print(f"  cd {lab_dir}")
        print("  kathara lstart")
        print("")
        print("Pulizia:")
        print("  kathara lclean")

        return 0

    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
