#!/usr/bin/env python3
"""Generatore di laboratori Kathara a partire da configurazioni YAML."""

import argparse
import ipaddress
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from src_gen_lab.Lexer import load_yaml
from src_gen_lab.Parser import validate_config, parse_yaml_configuration

FRR_DEVICE_TYPES = {"router", "switch", "firewall"}
BASE_IMAGE = "kathara/base"
FRR_IMAGE = "kathara/frr"


# ---------------------------------------------------------------------------
# Validazione e caricamento
# ---------------------------------------------------------------------------


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
    """Restituisce i protocolli FRR richiesti dal nodo.

    Retrocompatibilita':
    - frr.protocol: ospf|rip|bgp
    - frr.protocols: [ospf, bgp] oppure {ospf: {...}, bgp: {...}}
    - frr.daemons: {ospfd: true, ripd: true, bgpd: true}
    """
    frr = get_frr_section(node_data)
    protocols: list[str] = []

    if "protocol" in frr:
        protocols.append(str(frr["protocol"]).lower())

    raw_protocols = frr.get("protocols")
    if isinstance(raw_protocols, list):
        for proto in raw_protocols:
            protocols.append(str(proto).lower())
    elif isinstance(raw_protocols, dict):
        for proto, proto_data in raw_protocols.items():
            if isinstance(proto_data, dict) and proto_data.get("enabled", True) is False:
                continue
            protocols.append(str(proto).lower())

    for proto in ("ospf", "rip", "bgp"):
        proto_data = frr.get(proto)
        if isinstance(proto_data, dict) and proto_data.get("enabled", True) is not False:
            protocols.append(proto)

    daemons = frr.get("daemons", {})
    if isinstance(daemons, dict):
        if daemons.get("ospfd"):
            protocols.append("ospf")
        if daemons.get("ripd"):
            protocols.append("rip")
        if daemons.get("bgpd"):
            protocols.append("bgp")

    result: list[str] = []
    for proto in protocols:
        if proto not in result:
            result.append(proto)

    return result


def get_protocol_section(frr: dict[str, Any], protocol: str) -> dict[str, Any]:
    """Legge la configurazione specifica di protocollo, supportando piu' forme YAML."""
    section: dict[str, Any] = {}

    raw_protocols = frr.get("protocols")
    if isinstance(raw_protocols, dict) and isinstance(raw_protocols.get(protocol), dict):
        section.update(raw_protocols[protocol])

    raw_direct = frr.get(protocol)
    if isinstance(raw_direct, dict):
        section.update(raw_direct)

    return section


def unique_preserve_order(values: list[str]) -> list[str]:
    result: list[str] = []
    for value in values:
        if value not in result:
            result.append(value)
    return result


def interface_networks_by_marker(
    node_data: dict[str, Any],
    marker: str,
    marker_value: Any | None = None,
) -> list[str]:
    networks: list[str] = []

    for interface in node_data["interfaces"]:
        if marker not in interface:
            continue
        if marker_value is not None and interface.get(marker) != marker_value:
            continue

        ip_addr = interface.get("ip")
        if ip_addr:
            networks.append(get_network_from_ip(ip_addr))

    return unique_preserve_order(networks)


def get_ospf_network_area_pairs(node_data: dict[str, Any]) -> list[tuple[str, Any]]:
    """Reti OSPF: usa interface.ospf_area; in assenza, fallback legacy frr.area."""
    result: list[tuple[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for interface in node_data["interfaces"]:
        if "ospf_area" not in interface:
            continue
        ip_addr = interface.get("ip")
        if not ip_addr:
            continue
        network = get_network_from_ip(ip_addr)
        area = interface["ospf_area"]
        key = (network, str(area))
        if key not in seen:
            result.append((network, area))
            seen.add(key)

    if result:
        return result

    frr = get_frr_section(node_data)
    area = frr.get("area", 0)
    return [(network, area) for network in get_frr_networks(node_data)]


def get_rip_networks(node_data: dict[str, Any]) -> list[str]:
    """Reti RIP: usa interface.rip: true; in assenza, fallback legacy su tutte le interfacce."""
    marked = interface_networks_by_marker(node_data, "rip", True)
    return marked if marked else get_frr_networks(node_data)


def add_redistribute_lines(lines: list[str], section: dict[str, Any]) -> None:
    for proto in section.get("redistribute", []):
        lines.append(f" redistribute {proto}")


# ---------------------------------------------------------------------------
# lab.conf
# ---------------------------------------------------------------------------


def generate_lab_conf(config: dict[str, Any], wireshark_networks: list[str] | None = None) -> str:
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

    append_wireshark_to_lab_conf(lines, wireshark_networks or [])

    return "\n".join(lines).rstrip() + "\n"


# ---------------------------------------------------------------------------
# FRR
# ---------------------------------------------------------------------------


def get_frr_networks(node_data: dict[str, Any]) -> list[str]:
    frr = get_frr_section(node_data)
    networks = frr.get("networks")

    if networks:
        return [str(network) for network in networks]

    result: list[str] = []
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

    # Escape hatch: se serve, si puo' ancora scrivere frr.config a mano.
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

    common_router_id = frr.get("router_id")

    if "ospf" in protocols:
        ospf = get_protocol_section(frr, "ospf")
        router_id = ospf.get("router_id", common_router_id)

        lines.append("router ospf")
        if router_id:
            lines.append(f" ospf router-id {router_id}")

        for area in ospf.get("areas", frr.get("areas", [])):
            if not isinstance(area, dict):
                continue

            area_id = area.get("id")
            area_type = str(area.get("type", "")).lower()
            if area_id is None:
                continue

            if area_type in {"stub", "nssa"}:
                suffix = ""
                if area.get("no_summary") or area.get("totally_stub"):
                    suffix = " no-summary"
                lines.append(f" area {area_id} {area_type}{suffix}")

        for network, area in get_ospf_network_area_pairs(node_data):
            lines.append(f" network {network} area {area}")

        add_redistribute_lines(lines, ospf)
        lines.append("!")

    if "rip" in protocols:
        rip = get_protocol_section(frr, "rip")
        version = rip.get("version", frr.get("version", 2))

        lines.append("router rip")
        lines.append(f" version {version}")
        if rip.get("auto_summary", False) is False:
            lines.append(" no auto-summary")

        for network in rip.get("networks", get_rip_networks(node_data)):
            lines.append(f" network {network}")

        add_redistribute_lines(lines, rip)
        lines.append("!")

    if "bgp" in protocols:
        bgp = get_protocol_section(frr, "bgp")
        asn = bgp.get("asn", frr.get("asn"))
        if asn is None:
            raise ValueError(f"Il nodo {node_name} usa BGP ma manca frr.asn oppure frr.bgp.asn")

        router_id = bgp.get("router_id", common_router_id)

        lines.append(f"router bgp {asn}")
        if router_id:
            lines.append(f" bgp router-id {router_id}")

        if bgp.get("ebgp_requires_policy", frr.get("ebgp_requires_policy", False)) is False:
            lines.append(" no bgp ebgp-requires-policy")

        if bgp.get("network_import_check", frr.get("network_import_check", False)) is False:
            lines.append(" no bgp network import-check")

        for neighbor in bgp.get("neighbors", frr.get("neighbors", [])):
            lines.append(f" neighbor {neighbor['ip']} remote-as {neighbor['remote_as']}")

        for network in bgp.get("networks", frr.get("networks", [])):
            lines.append(f" network {network}")

        add_redistribute_lines(lines, bgp)
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
    lines: list[str] = []

    for index, interface in enumerate(node_data["interfaces"]):
        ip_addr = interface.get("ip")
        if ip_addr:
            lines.append(f"ip address add {ip_addr} dev eth{index}")

    if lines and (
        node_data.get("default_gateway")
        or node_data.get("routes")
        or is_frr_device(node_data)
    ):
        lines.append("")

    if node_data.get("default_gateway"):
        lines.append(f"ip route add default via {node_data['default_gateway']}")

    if node_data.get("routes"):
        for route in node_data["routes"]:
            lines.append(f"ip route add {route['to']} via {route['via']}")

    if is_frr_device(node_data):
        if lines and lines[-1] != "":
            lines.append("")
        lines.append("systemctl start frr")

    if node_data.get("commands"):
        if lines and lines[-1] != "":
            lines.append("")
        for command in node_data["commands"]:
            lines.append(str(command))

    return "\n".join(lines).rstrip() + "\n"


def copy_node_directories(
    config_path: Path,
    lab_dir: Path,
    nodes: dict[str, Any],
    enabled: bool,
) -> list[str]:
    """Copia nel lab le cartelle configs/<node_name>/ quando richiesto.

    Esempio: se il file YAML e' configs/test.yml e viene passato
    --import-node-dirs, la cartella configs/pc_a/ viene copiata in
    labs/<lab_name>/pc_a/. In Kathara quei file risultano disponibili
    nel container del nodo come /hostlab/<file>.
    """
    if not enabled:
        return []

    config_dir = config_path.parent
    imported: list[str] = []

    ignore = shutil.ignore_patterns(
        "__pycache__",
        "*.pyc",
        ".git",
        ".DS_Store",
    )

    for node_name in nodes:
        source_dir = config_dir / node_name
        if not source_dir.is_dir():
            continue

        destination_dir = lab_dir / node_name
        shutil.copytree(
            source_dir,
            destination_dir,
            dirs_exist_ok=True,
            ignore=ignore,
        )
        imported.append(node_name)

    return imported



def bash_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


# ---------------------------------------------------------------------------
# Wireshark real-time integrato nel lab.conf secondo il modello Kathara
# ---------------------------------------------------------------------------

WIRESHARK_NODE_NAME = "wireshark"
WIRESHARK_IMAGE = "lscr.io/linuxserver/wireshark"


def collision_domains(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Restituisce i collision domain presenti nel laboratorio."""
    domains: dict[str, dict[str, Any]] = {}

    for node_name, node_data in config["nodes"].items():
        for index, interface in enumerate(node_data.get("interfaces", [])):
            network = str(interface["network"])
            domain = domains.setdefault(network, {"name": network, "attachments": []})
            domain["attachments"].append(
                {
                    "node": node_name,
                    "type": node_type(node_data),
                    "eth": f"eth{index}",
                    "ip": interface.get("ip"),
                }
            )

    return list(domains.values())


def format_domain_summary(domain: dict[str, Any]) -> str:
    parts: list[str] = []
    for item in domain["attachments"]:
        label = f"{item['node']}:{item['eth']}"
        if item.get("ip"):
            label += f"({item['ip']})"
        parts.append(label)
    return ", ".join(parts)


def parse_network_selection(choice: str, domains: list[dict[str, Any]]) -> list[str] | None:
    """Interpreta la scelta utente: 0, all, indici, nomi o lista mista."""
    choice = choice.strip()
    if choice in {"", "0"}:
        return None

    if choice.lower() in {"all", "tutte", "tutti", "*"}:
        return [domain["name"] for domain in domains]

    by_name = {domain["name"]: domain["name"] for domain in domains}
    selected: list[str] = []

    for raw_token in choice.replace(";", ",").split(","):
        token = raw_token.strip()
        if not token:
            continue

        if token.isdigit():
            index = int(token)
            if not (1 <= index <= len(domains)):
                raise ValueError(f"Indice rete non valido: {token}")
            selected.append(domains[index - 1]["name"])
            continue

        if token not in by_name:
            raise ValueError(
                f"Collision domain '{token}' non trovato. "
                f"Reti disponibili: {', '.join(by_name.keys())}"
            )
        selected.append(token)

    return unique_preserve_order(selected)

def ask_yes_no(question: str, default: bool = False) -> bool:
    """Legge una risposta sì/no da terminale."""
    default_label = "S/n" if default else "s/N"
    answer = input(f"{question} [{default_label}]: ").strip().lower()

    if not answer:
        return default

    if answer in {"s", "si", "sì", "y", "yes"}:
        return True

    if answer in {"n", "no"}:
        return False

    print("Risposta non riconosciuta: considero 'no'.")
    return False

def normalize_wireshark_networks(value: Any) -> list[str]:
    """Normalizza reti/collision domain passati da YAML o CLI."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("wireshark.networks deve essere una stringa o una lista.")


def networks_attached_to_node(config: dict[str, Any], node_name: str) -> list[str]:
    """Restituisce le reti/collision domain a cui e' collegato un nodo."""
    nodes = config["nodes"]
    if node_name not in nodes:
        raise ValueError(
            f"Nodo '{node_name}' non trovato. Nodi disponibili: {', '.join(nodes.keys())}"
        )
    return unique_preserve_order(
        [str(interface["network"]) for interface in nodes[node_name].get("interfaces", [])]
    )


def parse_network_selection(choice: str, domains: list[dict[str, Any]], config: dict[str, Any]) -> list[str] | None:
    """Interpreta la scelta utente.

    Formati supportati:
    - 0 oppure invio: nessun Wireshark;
    - all / tutte / *: tutte le reti;
    - indici: 1 oppure 1,3,5;
    - nomi rete: lan_a oppure lan_a,r1_r2;
    - node:<nome>: tutte le reti collegate al nodo, es. node:r1.
    """
    choice = choice.strip()
    if choice in {"", "0"}:
        return None

    if choice.lower() in {"all", "tutte", "tutti", "*"}:
        return [domain["name"] for domain in domains]

    by_name = {domain["name"]: domain["name"] for domain in domains}
    selected: list[str] = []

    for raw_token in choice.replace(";", ",").split(","):
        token = raw_token.strip()
        if not token:
            continue

        lower_token = token.lower()
        if lower_token.startswith("node:") or lower_token.startswith("nodo:"):
            node_name = token.split(":", 1)[1].strip()
            selected.extend(networks_attached_to_node(config, node_name))
            continue

        if token.isdigit():
            index = int(token)
            if not (1 <= index <= len(domains)):
                raise ValueError(f"Indice rete non valido: {token}")
            selected.append(domains[index - 1]["name"])
            continue

        if token not in by_name:
            raise ValueError(
                f"Collision domain '{token}' non trovato. "
                f"Reti disponibili: {', '.join(by_name.keys())}"
            )
        selected.append(token)

    return unique_preserve_order(selected)


def ask_yes_no(question: str, default: bool = False) -> bool:
    """Legge una risposta sì/no da terminale."""
    default_label = "S/n" if default else "s/N"
    answer = input(f"{question} [{default_label}]: ").strip().lower()

    if not answer:
        return default

    if answer in {"s", "si", "sì", "y", "yes"}:
        return True

    if answer in {"n", "no"}:
        return False

    print("Risposta non riconosciuta: considero 'no'.")
    return False


def choose_wireshark_networks(
    config: dict[str, Any],
    requested_networks: str | None,
    wireshark_mode: str,
) -> list[str]:
    """Determina dove collegare Wireshark.

    Nel modello Kathara ufficiale Wireshark non viene agganciato a un router,
    ma viene collegato a uno o piu' collision domain del lab.conf. Per aiutare
    l'utente, il menu mostra anche quali nodi/interfacce appartengono a ciascuna rete.
    """
    domains = collision_domains(config)
    available = {domain["name"] for domain in domains}

    def validate_networks(networks: list[str]) -> list[str]:
        result = unique_preserve_order(networks)
        invalid = [network for network in result if network not in available]
        if invalid:
            raise ValueError(
                f"Collision domain Wireshark non presenti nel YAML: {', '.join(invalid)}. "
                f"Disponibili: {', '.join(sorted(available))}"
            )
        return result

    if requested_networks:
        # CLI: --wireshark-networks lan_a,r1_r2 oppure --wireshark-networks node:r1
        parsed = parse_network_selection(requested_networks, domains, config)
        return validate_networks(parsed or [])

    wireshark = config.get("wireshark", {})
    if isinstance(wireshark, dict):
        if wireshark.get("enabled") is False:
            return []

        yaml_networks = normalize_wireshark_networks(
            wireshark.get("networks", wireshark.get("network"))
        )
        if yaml_networks:
            # Supporta anche YAML: wireshark: { networks: "node:r1" }
            parsed = parse_network_selection(",".join(yaml_networks), domains, config)
            return validate_networks(parsed or [])

    if wireshark_mode == "disabled":
        return []

    if not sys.stdin.isatty():
        return []

    if wireshark_mode == "ask":
        print("\nStrumentazione Wireshark real-time")
        if not ask_yes_no("Vuoi implementare Wireshark real-time in questo laboratorio?", default=False):
            return []

    print("\nDove vuoi collegare Wireshark?")
    print("Nel modello Kathara Wireshark si collega a una o piu' reti/collision domain.")
    print("Scegli il punto del laboratorio da osservare.")
    print("\nCollision domain disponibili:")
    for i, domain in enumerate(domains, start=1):
        print(f"  {i}) {domain['name']}  | nodi: {format_domain_summary(domain)}")
    print("  all) collegare Wireshark a tutte le reti")
    print("  node:<nome>) collegare Wireshark a tutte le reti di un nodo, es. node:r1")
    print("  0) non generare Wireshark")

    choice = input("Scelta (es. 1 oppure 1,3 oppure lan_a,r1_r2 oppure node:r1): ")
    selected = parse_network_selection(choice, domains, config)
    return validate_networks(selected or [])


def append_wireshark_to_lab_conf(lines: list[str], networks: list[str]) -> None:
    """Aggiunge al lab.conf il nodo Wireshark come nel tutorial Kathara."""
    if not networks:
        return

    lines.append("# Wireshark real-time packet capture")
    for index, network in enumerate(networks):
        lines.append(f'{WIRESHARK_NODE_NAME}[{index}]="{network}"')
    lines.append(f'{WIRESHARK_NODE_NAME}[bridged]=true')
    lines.append(f'{WIRESHARK_NODE_NAME}[port]="3000:3000/tcp"')
    lines.append(f'{WIRESHARK_NODE_NAME}[image]="{WIRESHARK_IMAGE}"')
    lines.append("")



def remove_legacy_wireshark_scripts(lab_dir: Path) -> None:
    """Rimuove eventuali script Wireshark legacy non più necessari.

    Con l'integrazione real-time ufficiale, Wireshark è un nodo Kathara
    definito direttamente in lab.conf. Non servono più start_wireshark.sh,
    sniff.sh o stop_wireshark.sh.
    """
    for script_name in ("start_wireshark.sh", "sniff.sh", "stop_wireshark.sh"):
        script_path = lab_dir / script_name
        if script_path.exists():
            script_path.unlink()

def generate_lab(
    config_path: Path,
    output_dir: Path,
    clean: bool,
    force: bool,
    wireshark_networks: str | None,
    wireshark_mode: str,
    import_node_dirs: bool,
) -> tuple[Path, list[str], bool, list[str]]:
    config = load_yaml(config_path)
    validate_config(config)

    lab_name = str(config["lab_name"])
    nodes = config["nodes"]
    selected_wireshark_networks = choose_wireshark_networks(config, wireshark_networks, wireshark_mode)

    lab_dir = output_dir / lab_name
    prepare_lab_directory(lab_dir, clean=clean, force=force)

    (lab_dir / "lab.conf").write_text(generate_lab_conf(config, selected_wireshark_networks), encoding="utf-8")

    imported_node_dirs = copy_node_directories(
        config_path=config_path,
        lab_dir=lab_dir,
        nodes=nodes,
        enabled=import_node_dirs,
    )

    for node_name, node_data in nodes.items():
        startup_path = lab_dir / f"{node_name}.startup"
        startup_path.write_text(generate_startup(node_name, node_data), encoding="utf-8")
        os.chmod(startup_path, 0o644)

        if is_frr_device(node_data):
            write_frr_files(lab_dir, node_name, node_data)

    # Gli script legacy non sono più necessari: Wireshark è già nel lab.conf.
    remove_legacy_wireshark_scripts(lab_dir)
    wireshark_integrated = bool(selected_wireshark_networks)

    return lab_dir, selected_wireshark_networks, wireshark_integrated, imported_node_dirs


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

    parser.add_argument(
        "--wireshark-networks",
        default=None,
        help=(
            "Collision domain da collegare a Wireshark, separati da virgola. "
            "Esempio: lan_a,r1_r2. Se omesso, con --wireshark enabled/ask viene chiesto a terminale."
        ),
    )

    parser.add_argument(
        "--wireshark",
        choices=["ask", "enabled", "disabled"],
        default="ask",
        help=(
            "Modalità Wireshark real-time: "
            "ask chiede a terminale, enabled salta la prima domanda e chiede solo le reti, "
            "disabled non aggiunge Wireshark al lab.conf. Default: ask."
        ),
    )

    parser.add_argument(
        "--import-node-dirs",
        action="store_true",
        help=(
            "Importa nel laboratorio generato le cartelle dei nodi presenti accanto al file YAML. "
            "Esempio: configs/pc_a/ viene copiata in labs/<lab_name>/pc_a/."
        ),
    )

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config_path, _ = parse_yaml_configuration(args.lab, args.config_dir)
        lab_dir, selected_wireshark_networks, wireshark_generated, imported_node_dirs = generate_lab(
            config_path=config_path,
            output_dir=args.output_dir,
            clean=args.clean,
            force=args.force,
            wireshark_networks=args.wireshark_networks,
            wireshark_mode=args.wireshark,
            import_node_dirs=args.import_node_dirs,
        )

        print(f"[OK] Configurazione letta: {config_path}")
        print(f"[OK] Laboratorio generato: {lab_dir}")
        print("")
        print("Per avviare:")
        print(f"  cd {lab_dir}")
        print("  kathara lstart")
        print(" Oppure:")
        print(f"  ./start.sh {lab_dir}")
        print("")
        print("Per pulire:")
        print("  kathara lclean")
        print("")

        if wireshark_generated:
            print("Wireshark real-time integrato nel lab.conf.")
            print(f"Collision domain osservati: {', '.join(selected_wireshark_networks)}")
            print("Per avviare Wireshark basta avviare il laboratorio:")
            print("  kathara lstart")
            print("GUI: http://localhost:3000")
            print("Credenziali standard LinuxServer Wireshark: abc / abc")
        else:
            print("Wireshark non integrato nel laboratorio.")

        if imported_node_dirs:
            print("")
            print("Cartelle nodo importate:")
            for node_name in imported_node_dirs:
                print(f"  - {node_name}")

        return 0

    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
