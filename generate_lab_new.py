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

Uso:
    python3 generate_lab.py configs/file.yml oppure ./generate_lab.py configs/file.yml

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

        # FRR, nelle versioni recenti, richiede policy esplicite per eBGP.
        # Nei laboratori didattici abilitiamo automaticamente l'annuncio/ricezione
        # senza policy, altrimenti i neighbor possono stabilirsi ma le rotte non
        # vengono accettate o propagate.
        if frr.get("ebgp_requires_policy", False) is False:
            lines.append(" no bgp ebgp-requires-policy")

        # Permette di annunciare le reti indicate in frr.networks senza blocchi
        # dovuti al controllo di import/check sul network statement.
        if frr.get("network_import_check", False) is False:
            lines.append(" no bgp network import-check")

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
# Wireshark automatico per lab Kathara
# ---------------------------------------------------------------------------

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
        # In modalità non interattiva non facciamo domande.
        # Per generare Wireshark usare --sniff-node oppure wireshark.sniff_node nel YAML.
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
CONTAINER="wireshark-sniffer-$LAB_NAME"

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
    """
    Genera gli script Wireshark se sniff_node è valorizzato.

    Ritorna True se gli script sono stati generati, False altrimenti.
    Se sniff_node è None, elimina eventuali script Wireshark residui da
    generazioni precedenti, così la scelta "0" nel menu è effettiva.
    """
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


# ---------------------------------------------------------------------------
# Generazione laboratorio
# ---------------------------------------------------------------------------

def generate_lab(
    config_path: Path,
    output_dir: Path,
    clean: bool,
    force: bool,
    sniff_node: str | None,
    wireshark_mode: str,
) -> tuple[Path, str | None, bool]:
    config = load_yaml(config_path)
    validate_config(config)

    lab_name = str(config["lab_name"])
    nodes = config["nodes"]
    selected_sniff_node = choose_sniff_node(config, sniff_node, wireshark_mode)

    lab_dir = output_dir / lab_name
    prepare_lab_directory(lab_dir, clean=clean, force=force)

    (lab_dir / "lab.conf").write_text(generate_lab_conf(config), encoding="utf-8")

    for node_name, node_data in nodes.items():
        startup_path = lab_dir / f"{node_name}.startup"
        startup_path.write_text(generate_startup(node_name, node_data), encoding="utf-8")
        os.chmod(startup_path, 0o644)

        if is_frr_device(node_data):
            write_frr_files(lab_dir, node_name, node_data)

    wireshark_generated = write_wireshark_scripts(lab_dir, lab_name, selected_sniff_node)

    return lab_dir, selected_sniff_node, wireshark_generated


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

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        config_path = resolve_config_path(args.lab, args.config_dir)
        lab_dir, selected_sniff_node, wireshark_generated = generate_lab(
            config_path=config_path,
            output_dir=args.output_dir,
            clean=args.clean,
            force=args.force,
            sniff_node=args.sniff_node,
            wireshark_mode=args.wireshark,
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
        print("")

        if wireshark_generated:
            print(f"Script Wireshark generati per il nodo: {selected_sniff_node}")
            print("  ./start_wireshark.sh [any|eth0|eth1|...]")
            print("  ./sniff.sh")
            print("  ./stop_wireshark.sh")
        else:
            print("Script Wireshark non generati.")

        return 0

    except Exception as exc:
        print(f"[ERRORE] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
