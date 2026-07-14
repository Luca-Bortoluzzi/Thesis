#!/usr/bin/env python3
"""Kathara lab generator driven by YAML configurations."""

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
# Validation and loading
# ---------------------------------------------------------------------------


def prepare_lab_directory(lab_dir: Path, clean: bool, force: bool) -> None:
    if lab_dir.exists() and clean:
        shutil.rmtree(lab_dir)

    if lab_dir.exists() and not force and not clean:
        raise FileExistsError(
            f"Directory {lab_dir} already exists. "
            "Use --force to overwrite it or --clean to remove and regenerate it."
        )

    lab_dir.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------------------
# Utilities
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
        raise ValueError("The frr section must be a dictionary.")
    return frr


def get_frr_protocols(node_data: dict[str, Any]) -> list[str]:
    """Returns the FRR protocols requested by a node.

    Backward compatibility:
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
    """Reads a protocol-specific configuration while supporting multiple YAML forms."""
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
            raise ValueError(f"Node {node_name} uses BGP but lacks frr.asn or frr.bgp.asn")

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
        lines.extend([
            "systemctl start frr",
        ])

    if node_data.get("commands"):
        if lines and lines[-1] != "":
            lines.append("")
        for command in node_data["commands"]:
            lines.append(str(command))

    return "\n".join(lines).rstrip() + "\n"


def candidate_import_directories(config_path: Path) -> list[Path]:
    config_dir = config_path.parent
    return [
        path
        for path in sorted(config_dir.iterdir(), key=lambda item: item.name.lower())
        if path.is_dir()
        and path.name not in {"__pycache__"}
        and not path.name.startswith(".")
    ]


def validate_import_directories(config_path: Path, nodes: dict[str, Any], enabled: bool) -> None:
    if not enabled:
        return

    node_names = set(nodes)
    invalid_dirs = [
        path.name
        for path in candidate_import_directories(config_path)
        if path.name not in node_names
    ]
    if invalid_dirs:
        raise ValueError(
            "Directories to import without a corresponding node in the lab: "
            + ", ".join(invalid_dirs)
            + ". Rename/remove the directory or add a node with the same name to the YAML file."
        )


def copy_node_directories(
    config_path: Path,
    lab_dir: Path,
    nodes: dict[str, Any],
    enabled: bool,
) -> list[str]:
    """Copia nel lab le cartelle configs/<node_name>/ quando richiesto.

    Example: if the YAML file is configs/test.yml and --import-node-dirs is
    passed, configs/pc_a/ is copied to labs/<lab_name>/pc_a/. In Kathara these
    files are available in the node container as /hostlab/<file>.
    """
    if not enabled:
        return []

    validate_import_directories(config_path, nodes, enabled=True)

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
    """Returns the collision domains defined in the lab."""
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


def normalize_wireshark_networks(value: Any) -> list[str]:
    """Normalizes networks/collision domains passed through YAML or the CLI."""
    if value is None:
        return []
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    raise ValueError("wireshark.networks must be a string or a list.")


def networks_attached_to_node(config: dict[str, Any], node_name: str) -> list[str]:
    """Returns the networks/collision domains connected to a node."""
    nodes = config["nodes"]
    if node_name not in nodes:
        raise ValueError(
            f"Node '{node_name}' not found. Available nodes: {', '.join(nodes.keys())}"
        )
    return unique_preserve_order(
        [str(interface["network"]) for interface in nodes[node_name].get("interfaces", [])]
    )


def parse_network_selection(choice: str, domains: list[dict[str, Any]], config: dict[str, Any]) -> list[str] | None:
    """Parses a user selection.

    Supported formats:
    - 0 or ENTER: no Wireshark;
    - all / *: all networks;
    - indexes: 1 or 1,3,5;
    - network names: lan_a or lan_a,r1_r2;
    - node:<name>: all networks connected to a node, e.g. node:r1.
    """
    choice = choice.strip()
    if choice in {"", "0"}:
        return None

    if choice.lower() in {"all", "*"}:
        return [domain["name"] for domain in domains]

    by_name = {domain["name"]: domain["name"] for domain in domains}
    selected: list[str] = []

    for raw_token in choice.replace(";", ",").split(","):
        token = raw_token.strip()
        if not token:
            continue

        lower_token = token.lower()
        if lower_token.startswith("node:"):
            node_name = token.split(":", 1)[1].strip()
            selected.extend(networks_attached_to_node(config, node_name))
            continue

        if token.isdigit():
            index = int(token)
            if not (1 <= index <= len(domains)):
                raise ValueError(f"Invalid network index: {token}")
            selected.append(domains[index - 1]["name"])
            continue

        if token not in by_name:
            raise ValueError(
                f"Collision domain '{token}' not found. "
                f"Available networks: {', '.join(by_name.keys())}"
            )
        selected.append(token)

    return unique_preserve_order(selected)


def ask_yes_no(question: str, default: bool = False) -> bool:
    """Reads a yes/no answer from the terminal."""
    default_label = "Y/n" if default else "y/N"
    answer = input(f"{question} [{default_label}]: ").strip().lower()

    if not answer:
        return default

    if answer in {"y", "yes"}:
        return True

    if answer in {"n", "no"}:
        return False

    print("Unrecognized answer: assuming 'no'.")
    return False


def choose_wireshark_networks(
    config: dict[str, Any],
    requested_networks: str | None,
    wireshark_mode: str,
) -> list[str]:
    """Determines where to attach Wireshark.

    In the official Kathara model, Wireshark is not attached to a router. It is
    connected to one or more collision domains from lab.conf. The menu also
    shows which nodes/interfaces belong to each network.
    """
    domains = collision_domains(config)
    available = {domain["name"] for domain in domains}

    def validate_networks(networks: list[str]) -> list[str]:
        result = unique_preserve_order(networks)
        invalid = [network for network in result if network not in available]
        if invalid:
            raise ValueError(
                f"Wireshark collision domains not present in the YAML: {', '.join(invalid)}. "
                f"Available: {', '.join(sorted(available))}"
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
            # Also supports YAML: wireshark: { networks: "node:r1" }
            parsed = parse_network_selection(",".join(yaml_networks), domains, config)
            return validate_networks(parsed or [])

    if wireshark_mode == "disabled":
        return []

    if not sys.stdin.isatty():
        return []

    if wireshark_mode == "ask":
        print("\nReal-time Wireshark integration")
        if not ask_yes_no("Enable real-time Wireshark integration for this lab?", default=False):
            return []

    print("\nWhere should Wireshark be attached?")
    print("In the Kathara model, Wireshark connects to one or more networks/collision domains.")
    print("Select the observation point in the lab.")
    print("\nAvailable collision domains:")
    for i, domain in enumerate(domains, start=1):
        print(f"  {i}) {domain['name']}  | nodes: {format_domain_summary(domain)}")
    print("  all) attach Wireshark to every network")
    print("  node:<name>) attach Wireshark to every network of a node, e.g. node:r1")
    print("  0) do not generate Wireshark")

    choice = input("Selection (e.g. 1, 1,3, lan_a,r1_r2, or node:r1): ")
    selected = parse_network_selection(choice, domains, config)
    return validate_networks(selected or [])


def append_wireshark_to_lab_conf(lines: list[str], networks: list[str]) -> None:
    """Adds the Wireshark node to lab.conf as described in the Kathara tutorial."""
    if not networks:
        return

    lines.append("# Wireshark real-time packet capture")
    for index, network in enumerate(networks):
        lines.append(f'{WIRESHARK_NODE_NAME}[{index}]="{network}"')
    lines.append(f'{WIRESHARK_NODE_NAME}[bridged]=true')
    lines.append(f'{WIRESHARK_NODE_NAME}[port]="3000:3000/tcp"')
    lines.append(f'{WIRESHARK_NODE_NAME}[image]="{WIRESHARK_IMAGE}"')
    lines.append(f'{WIRESHARK_NODE_NAME}[num_terms]=0')
    lines.append("")



def remove_legacy_wireshark_scripts(lab_dir: Path) -> None:
    """Removes obsolete Wireshark helper scripts.

    With official real-time integration, Wireshark is a Kathara node defined
    directly in lab.conf. start_wireshark.sh, sniff.sh, and stop_wireshark.sh
    are no longer needed.
    """
    for script_name in ("start_wireshark.sh", "sniff.sh", "stop_wireshark.sh"):
        script_path = lab_dir / script_name
        if script_path.exists():
            script_path.unlink()



# ---------------------------------------------------------------------------
# Manual zombies.txt generation for DoS/C2 simulations
# ---------------------------------------------------------------------------


def split_ip_tokens(value: str) -> list[str]:
    """Splits IP addresses separated by spaces, commas, semicolons, or newlines."""
    tokens: list[str] = []
    for chunk in value.replace(";", ",").replace("\n", ",").split(","):
        tokens.extend(part.strip() for part in chunk.split() if part.strip())
    return tokens


def normalize_ip(value: str) -> str:
    """Validates a user-provided IP address and removes an optional CIDR prefix."""
    try:
        if "/" in value:
            return str(ipaddress.ip_interface(value).ip)
        return str(ipaddress.ip_address(value))
    except ValueError as exc:
        raise ValueError(f"Invalid zombie IP address: {value}") from exc


def parse_zombie_ips(value: str) -> list[str]:
    """Returns valid, unique IP addresses while preserving input order."""
    ips: list[str] = []
    for token in split_ip_tokens(value):
        ip = normalize_ip(token)
        if ip not in ips:
            ips.append(ip)
    return ips


def default_c2_node(nodes: dict[str, Any]) -> str | None:
    """Suggests the C2/attacker node on which to mount zombies.txt."""
    for node_name in nodes:
        if "attacker" in node_name.lower():
            return node_name
    for node_name in nodes:
        if "c2" in node_name.lower() or "master" in node_name.lower():
            return node_name
    return None


def choose_c2_node(nodes: dict[str, Any], requested: str | None, interactive: bool) -> str:
    """Selects the node where zombies.txt is written."""
    if requested:
        if requested not in nodes:
            raise ValueError(
                f"C2/attacker node '{requested}' not found. "
                f"Available nodes: {', '.join(nodes.keys())}"
            )
        return requested

    proposed = default_c2_node(nodes)
    if interactive:
        if proposed:
            answer = input(f"C2/attacker node for zombies.txt [{proposed}]: ").strip()
            node_name = answer or proposed
        else:
            print("Available nodes:")
            for node_name in nodes:
                print(f"  - {node_name}")
            node_name = input("C2/attacker node for zombies.txt: ").strip()

        if not node_name:
            raise ValueError("No C2/attacker node was specified.")
        if node_name not in nodes:
            raise ValueError(
                f"C2/attacker node '{node_name}' not found. "
                f"Available nodes: {', '.join(nodes.keys())}"
            )
        return node_name

    if proposed:
        return proposed

    raise ValueError(
        "Unable to select the C2/attacker node automatically. "
        "Use --zombies-node <node_name>."
    )


def ask_zombie_ips() -> list[str]:
    """Prompts the user for zombie IP addresses."""
    print("\nManual zombies.txt generation")
    print("Enter zombie IP addresses separated by spaces, commas, or semicolons.")
    print("You can also enter one IP per line; submit an empty line to finish.")

    lines: list[str] = []
    first = input("Zombie IP: ").strip()
    if not first:
        return []
    lines.append(first)

    while True:
        line = input("Additional zombie IP [ENTER to finish]: ").strip()
        if not line:
            break
        lines.append(line)

    return parse_zombie_ips("\n".join(lines))


def write_manual_zombies_file(
    lab_dir: Path,
    nodes: dict[str, Any],
    mode: str,
    zombies_node: str | None,
    zombies_ips_arg: str | None,
) -> Path | None:
    """Creates zombies.txt only from IPs supplied manually or through the CLI.

    It does not extract IP addresses automatically from YAML nodes.
    """
    if mode == "disabled":
        return None

    interactive = sys.stdin.isatty()

    zombie_ips: list[str] = []
    if zombies_ips_arg:
        zombie_ips = parse_zombie_ips(zombies_ips_arg)
    elif mode == "manual":
        if not interactive:
            raise ValueError(
                "--zombies manual requires an interactive terminal or --zombies-ips."
            )
        zombie_ips = ask_zombie_ips()
    else:
        raise ValueError(f"Invalid zombies mode: {mode}")

    if not zombie_ips:
        print("[Framework] zombies.txt was not generated: no zombie IP addresses were provided.")
        return None

    c2_node = choose_c2_node(nodes, zombies_node, interactive=interactive)
    c2_dir = lab_dir / c2_node
    c2_dir.mkdir(parents=True, exist_ok=True)

    zombies_file_path = c2_dir / "zombies.txt"
    zombies_file_path.write_text("\n".join(zombie_ips) + "\n", encoding="utf-8")

    print(
        f"[Framework] Manually generated zombies.txt with {len(zombie_ips)} IP addresses "
        f"for node {c2_node}: {zombies_file_path}"
    )
    return zombies_file_path

def generate_lab(
    config_path: Path,
    output_dir: Path,
    clean: bool,
    force: bool,
    wireshark_networks: str | None,
    wireshark_mode: str,
    import_dirs: bool,
    zombies_mode: str,
    zombies_node: str | None,
    zombies_ips: str | None,
) -> tuple[Path, list[str], bool, list[str]]:
    config = load_yaml(config_path)
    validate_config(config)

    lab_name = str(config["lab_name"])
    nodes = config["nodes"]
    validate_import_directories(config_path, nodes, enabled=import_dirs)
    selected_wireshark_networks = choose_wireshark_networks(config, wireshark_networks, wireshark_mode)

    if selected_wireshark_networks and WIRESHARK_NODE_NAME in nodes:
        raise ValueError(
            f"The YAML already contains a node named '{WIRESHARK_NODE_NAME}'. "
            "Rename that node or change WIRESHARK_NODE_NAME in the generator; "
            "otherwise lab.conf would contain duplicate definitions."
        )

    lab_dir = output_dir / lab_name
    prepare_lab_directory(lab_dir, clean=clean, force=force)

    (lab_dir / "lab.conf").write_text(generate_lab_conf(config, selected_wireshark_networks), encoding="utf-8")

    imported_node_dirs = copy_node_directories(
        config_path=config_path,
        lab_dir=lab_dir,
        nodes=nodes,
        enabled=import_dirs,
    )

    # Optional, manual zombies.txt generation.
    # Default: disabled, with no terminal prompts.
    # IP addresses are not extracted automatically from YAML; the user supplies them.
    write_manual_zombies_file(
        lab_dir=lab_dir,
        nodes=nodes,
        mode=zombies_mode,
        zombies_node=zombies_node,
        zombies_ips_arg=zombies_ips,
    )

    for node_name, node_data in nodes.items():
        startup_path = lab_dir / f"{node_name}.startup"
        startup_path.write_text(generate_startup(node_name, node_data), encoding="utf-8")
        os.chmod(startup_path, 0o644)

        if is_frr_device(node_data):
            write_frr_files(lab_dir, node_name, node_data)

    # Legacy scripts are unnecessary because Wireshark is already in lab.conf.
    remove_legacy_wireshark_scripts(lab_dir)
    wireshark_integrated = bool(selected_wireshark_networks)

    return lab_dir, selected_wireshark_networks, wireshark_integrated, imported_node_dirs


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate lab.conf, .startup files, and FRR configurations for Kathara."
    )

    parser.add_argument(
        "lab",
        help="YAML file to read or lab name.",
    )

    parser.add_argument(
        "--config-dir",
        default="configs",
        type=Path,
        help="Directory used to search for YAML files when only a lab name is supplied. Default: configs",
    )

    parser.add_argument(
        "--output-dir",
        "-o",
        default="labs",
        type=Path,
        help="Output directory for generated labs. Default: labs",
    )

    parser.add_argument(
        "--clean",
        action="store_true",
        help="Remove and recreate the lab directory if it already exists.",
    )

    parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite files if the lab directory already exists.",
    )

    parser.add_argument(
        "--wireshark-networks",
        default=None,
        help=(
            "Comma-separated collision domains to attach to Wireshark. "
            "Example: lan_a,r1_r2. If omitted, --wireshark enabled/ask prompts on the terminal."
        ),
    )

    parser.add_argument(
        "--wireshark",
        choices=["ask", "enabled", "disabled"],
        default="ask",
        help=(
            "Real-time Wireshark mode: "
            "ask prompts on the terminal, enabled skips the first question and asks only for networks, "
            "disabled does not add Wireshark to lab.conf. Default: ask."
        ),
    )

    parser.add_argument(
        "--import-dirs",
        action="store_true",
        help=(
            "Import node directories next to the YAML file into the generated lab. "
            "Example: configs/pc_a/ is copied to labs/<lab_name>/pc_a/."
        ),
    )


    parser.add_argument(
        "--zombies",
        choices=["manual", "disabled"],
        default="disabled",
        help=(
            "zombies.txt handling: "
            "disabled does not generate it or prompt; "
            "manual forces manual input or uses --zombies-ips. "
            "Default: disabled."
        ),
    )

    parser.add_argument(
        "--zombies-node",
        default=None,
        help=(
            "C2/attacker node where zombies.txt is written. "
            "If omitted, the first node containing 'attacker' in its name is proposed."
        ),
    )

    parser.add_argument(
        "--zombies-ips",
        default=None,
        help=(
            "Zombie IP addresses written to zombies.txt, separated by commas, spaces, or semicolons. "
            "Example: --zombies-ips '10.0.1.10,10.0.1.11'."
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
            import_dirs=args.import_dirs,
            zombies_mode=args.zombies,
            zombies_node=args.zombies_node,
            zombies_ips=args.zombies_ips,
        )

        print(f"[OK] Configuration read: {config_path}")
        print(f"[OK] Lab generated: {lab_dir}")
        print("")
        print("To start:")
        print(f"  ./start.sh {lab_dir}")
        print("Or:")
        print(f"  cd {lab_dir}")
        print("  kathara lstart")
        print("")
        print("To stop:")
        print(f"  ./stop.sh {lab_dir}")
        print("Or:")
        print(f"  cd {lab_dir}")
        print("  kathara lclean")
        print("")

        if wireshark_generated:
            print("Real-time Wireshark is integrated in lab.conf.")
            print(f"Observed collision domains: {', '.join(selected_wireshark_networks)}")
            print("Start the lab to start Wireshark:")
            print("  kathara lstart")
            print("GUI: http://localhost:3000")
            print("Default LinuxServer Wireshark credentials: abc / abc")
        else:
            print("Wireshark is not integrated in the lab.")

        if imported_node_dirs:
            print("")
            print("Imported node directories:")
            for node_name in imported_node_dirs:
                print(f"  - {node_name}")

        return 0

    except Exception as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
