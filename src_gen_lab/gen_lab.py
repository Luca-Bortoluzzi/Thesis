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


def sniffable_nodes(config: dict[str, Any]) -> list[str]:
    nodes = config["nodes"]
    preferred = [name for name, data in nodes.items() if is_frr_device(data)]
    return preferred if preferred else list(nodes.keys())


def ask_yes_no(question: str, default: bool = False) -> bool:
    default_label = "S/n" if default else "s/N"
    answer = input(f"{question} [{default_label}]: ").strip().lower()
    if not answer:
        return default
    return answer in {"s", "si", "sì", "y", "yes"}


def choose_sniff_node(
    config: dict[str, Any],
    requested: str | None,
    wireshark_mode: str,
) -> str | None:
    nodes = config["nodes"]

    if requested:
        if requested not in nodes:
            raise ValueError(
                f"Nodo di sniffing '{requested}' non trovato nel YAML. "
                f"Nodi disponibili: {', '.join(nodes.keys())}"
            )
        return requested

    wireshark = config.get("wireshark", {})
    if isinstance(wireshark, dict) and wireshark.get("enabled") is False:
        return None

    if isinstance(wireshark, dict) and wireshark.get("sniff_node"):
        node = str(wireshark["sniff_node"])
        if node not in nodes:
            raise ValueError(
                f"wireshark.sniff_node '{node}' non trovato nel YAML. "
                f"Nodi disponibili: {', '.join(nodes.keys())}"
            )
        return node

    if wireshark_mode == "disabled":
        return None

    candidates = sniffable_nodes(config)

    if not sys.stdin.isatty():
        return None

    if wireshark_mode == "ask":
        print("\nStrumentazione Wireshark")
        if not ask_yes_no("Vuoi generare gli script automatici Wireshark per questo laboratorio?", default=False):
            return None

    print("\nNodi disponibili per lo sniffing Wireshark:")
    for i, name in enumerate(candidates, start=1):
        data = nodes[name]
        interfaces = data.get("interfaces", [])
        nets = ", ".join(
            str(iface.get("network", f"eth{idx}"))
            for idx, iface in enumerate(interfaces)
        )
        ip_list = ", ".join(
            str(iface.get("ip"))
            for iface in interfaces
            if iface.get("ip")
        )
        extra = f" | IP: {ip_list}" if ip_list else ""
        print(f"  {i}) {name}  [{node_type(data)}]  reti: {nets}{extra}")

    print("  0) non generare script Wireshark")
    choice = input("Scegli il dispositivo su cui fare sniffing: ").strip()

    if choice in {"", "0"}:
        return None

    try:
        index = int(choice)
        if 1 <= index <= len(candidates):
            return candidates[index - 1]
    except ValueError:
        pass

    if choice in candidates:
        return choice

    raise ValueError(f"Scelta non valida: {choice}")


def generate_start_wireshark_script(lab_name: str, sniff_node: str) -> str:
    quoted_lab = bash_quote(lab_name)
    quoted_node = bash_quote(sniff_node)
    return f'''#!/bin/bash
set -e

IMAGE="lscr.io/linuxserver/wireshark:latest"
LAB_NAME={quoted_lab}
SNIFF_NODE={quoted_node}
IFACE="${{1:-any}}"

LAB_DIR="$(cd "$(dirname "$0")" && pwd)"
CAPTURE_DIR="$LAB_DIR/captures"
CONFIG_DIR="$HOME/wireshark-config-$LAB_NAME"

mkdir -p "$CAPTURE_DIR" "$CONFIG_DIR"
chmod 777 "$CAPTURE_DIR" || true

TARGET_CONTAINER=$(docker ps --format "{{{{.Names}}}}" | grep -E "^kathara_.*_${{SNIFF_NODE}}_" | head -n 1)

if [ -z "$TARGET_CONTAINER" ]; then
  echo "[ERRORE] Nessun container Kathara trovato per nodo: $SNIFF_NODE"
  echo "[INFO] Container Kathara attivi:"
  docker ps --format "{{{{.Names}}}}" | grep '^kathara_' || true
  echo ""
  echo "Avvia prima il lab dalla cartella:"
  echo "  kathara lstart"
  exit 1
fi

echo "[OK] Nodo sniffing: $SNIFF_NODE"
echo "[OK] Container target: $TARGET_CONTAINER"
echo "[OK] Interfaccia: $IFACE"
echo "[OK] Cartella catture: $CAPTURE_DIR"

docker rm -f "wireshark-sniffer-$LAB_NAME" "wireshark-gui-$LAB_NAME" 2>/dev/null || true

docker pull "$IMAGE"

docker run -d \
  --name "wireshark-sniffer-$LAB_NAME" \
  --net=container:"$TARGET_CONTAINER" \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  -e TZ=Europe/Rome \
  -e IFACE="$IFACE" \
  -e SNIFF_NODE="$SNIFF_NODE" \
  -v "$CAPTURE_DIR:/captures" \
  --restart unless-stopped \
  --entrypoint /bin/bash \
  "$IMAGE" \
  -lc '
    set -e
    echo "[SNIFFER] Avviato nel namespace del nodo Kathara"
    echo "[SNIFFER] Interfacce disponibili:"
    ip link show

    mkdir -p /captures
    chmod 777 /captures || true

    if ! touch /captures/test_write.tmp 2>/dev/null; then
      echo "[ERRORE] /captures non è scrivibile."
      ls -ld /captures || true
      sleep infinity
    fi
    rm -f /captures/test_write.tmp

    while true; do
      chmod -R a+rwx /captures 2>/dev/null || true
      sleep 2
    done &

    FILE="/captures/${{SNIFF_NODE}}_$(date +%Y%m%d_%H%M%S).pcapng"
    echo "[SNIFFER] Cattura su interfaccia: $IFACE"
    echo "[SNIFFER] File output: $FILE"

    if command -v dumpcap >/dev/null 2>&1; then
      exec dumpcap -p -i "$IFACE" -w "$FILE" -b filesize:10240 -b files:20
    elif command -v tshark >/dev/null 2>&1; then
      exec tshark -p -i "$IFACE" -w "$FILE" -b filesize:10240 -b files:20
    else
      echo "[ERRORE] Né dumpcap né tshark sono disponibili nell’immagine."
      sleep infinity
    fi
  '

docker run -d \
  --name "wireshark-gui-$LAB_NAME" \
  -p 3000:3000 \
  -p 3001:3001 \
  --cap-add=NET_ADMIN \
  --cap-add=NET_RAW \
  -e PUID=0 \
  -e PGID=0 \
  -e TZ=Europe/Rome \
  -v "$CONFIG_DIR:/config" \
  -v "$CAPTURE_DIR:/captures" \
  --shm-size="1gb" \
  --restart unless-stopped \
  "$IMAGE"

echo ""
echo "[OK] Wireshark automatico avviato."
echo "Log sniffer:"
echo "  ./sniff.sh"
echo "  docker logs -f wireshark-sniffer-$LAB_NAME"
echo "GUI Wireshark:"
echo "  http://localhost:3000"
echo "Catture host:"
echo "  $CAPTURE_DIR"
echo "Dentro Wireshark apri:"
echo "  /captures"
'''


def generate_sniff_wireshark_script(lab_name: str) -> str:
    quoted_lab = bash_quote(lab_name)
    return f"""#!/bin/bash
LAB_NAME={quoted_lab}
CONTAINER=\"wireshark-sniffer-$LAB_NAME\"

if ! docker ps --format "{{{{.Names}}}}" | grep -qx "$CONTAINER"; then
  echo "[ERRORE] Container sniffer non attivo: $CONTAINER"
  echo "Avvia prima Wireshark con:"
  echo "  ./start_wireshark.sh [any|eth0|eth1|...]"
  echo ""
  echo "Container Wireshark disponibili:"
  docker ps --format "{{{{.Names}}}}" | grep '^wireshark-' || true
  exit 1
fi

echo "[OK] Seguo i log dello sniffer: $CONTAINER"
echo "[INFO] Premi CTRL+C per uscire dai log. Lo sniffing continuerà in background."
echo ""
exec docker logs -f "$CONTAINER"
"""


def generate_stop_wireshark_script(lab_name: str) -> str:
    quoted_lab = bash_quote(lab_name)
    return f'''#!/bin/bash
LAB_NAME={quoted_lab}
docker rm -f "wireshark-sniffer-$LAB_NAME" "wireshark-gui-$LAB_NAME" 2>/dev/null || true
echo "[OK] Wireshark fermato per lab: $LAB_NAME"
'''


def write_wireshark_scripts(lab_dir: Path, lab_name: str, sniff_node: str | None) -> bool:
    start_path = lab_dir / "start_wireshark.sh"
    stop_path = lab_dir / "stop_wireshark.sh"
    sniff_path = lab_dir / "sniff.sh"

    if not sniff_node:
        for script_path in (start_path, stop_path, sniff_path):
            if script_path.exists():
                script_path.unlink()
        return False

    start_path.write_text(generate_start_wireshark_script(lab_name, sniff_node), encoding="utf-8")
    stop_path.write_text(generate_stop_wireshark_script(lab_name), encoding="utf-8")
    sniff_path.write_text(generate_sniff_wireshark_script(lab_name), encoding="utf-8")

    os.chmod(start_path, 0o755)
    os.chmod(stop_path, 0o755)
    os.chmod(sniff_path, 0o755)
    return True


def generate_lab(
    config_path: Path,
    output_dir: Path,
    clean: bool,
    force: bool,
    sniff_node: str | None,
    wireshark_mode: str,
    import_node_dirs: bool,
) -> tuple[Path, str | None, bool, list[str]]:
    config = load_yaml(config_path)
    validate_config(config)

    lab_name = str(config["lab_name"])
    nodes = config["nodes"]
    selected_sniff_node = choose_sniff_node(config, sniff_node, wireshark_mode)

    lab_dir = output_dir / lab_name
    prepare_lab_directory(lab_dir, clean=clean, force=force)

    (lab_dir / "lab.conf").write_text(generate_lab_conf(config), encoding="utf-8")

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

    wireshark_generated = write_wireshark_scripts(lab_dir, lab_name, selected_sniff_node)

    return lab_dir, selected_sniff_node, wireshark_generated, imported_node_dirs


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
        "--sniff-node",
        default=None,
        help="Nodo Kathara su cui generare lo sniffing Wireshark automatico, ad esempio r1, r2, router.",
    )

    parser.add_argument(
        "--wireshark",
        choices=["ask", "enabled", "disabled"],
        default="ask",
        help=(
            "Modalità generazione script Wireshark: "
            "ask chiede a terminale, enabled salta la prima domanda e chiede solo il nodo, "
            "disabled non genera script. Default: ask."
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
        lab_dir, selected_sniff_node, wireshark_generated, imported_node_dirs = generate_lab(
            config_path=config_path,
            output_dir=args.output_dir,
            clean=args.clean,
            force=args.force,
            sniff_node=args.sniff_node,
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
            print(f"Script Wireshark generati per il nodo: {selected_sniff_node}")
            print("  ./start_wireshark.sh [any|eth0|eth1|...]")
            print("  ./sniff.sh")
            print("  ./stop_wireshark.sh")
        else:
            print("Script Wireshark non generati.")

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
