#!/usr/bin/env python3
"""Syntactic and semantic validation for Kathara lab YAML configurations."""

from __future__ import annotations

import ipaddress
import re
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from src_gen_lab.Lexer import load_yaml, resolve_config_path


SUPPORTED_NODE_TYPES = {"host", "router", "switch", "firewall"}
SUPPORTED_FRR_PROTOCOLS = {"ospf", "rip", "bgp"}
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


@dataclass(frozen=True)
class ValidationIssue:
    severity: str
    code: str
    message: str


@dataclass
class SemanticValidationReport:
    node_count: int
    collision_domain_count: int = 0
    interface_count: int = 0
    ospf_nodes: int = 0
    bgp_nodes: int = 0
    import_dirs_checked: bool = False
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [issue for issue in self.issues if issue.severity == "WARN"]

    @property
    def valid(self) -> bool:
        return not self.errors

    def error(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue("ERROR", code, message))

    def warn(self, code: str, message: str) -> None:
        self.issues.append(ValidationIssue("WARN", code, message))

    def has_error(self, *prefixes: str) -> bool:
        return any(
            any(issue.code.startswith(prefix) for prefix in prefixes)
            for issue in self.errors
        )


class SemanticValidationError(ValueError):
    def __init__(self, report: SemanticValidationReport):
        self.report = report
        messages = "; ".join(issue.message for issue in report.errors)
        super().__init__(messages or "Semantic validation failed.")


def validate_config(config: dict[str, Any]) -> None:
    """Validate the minimum YAML structure required by the generator."""
    if "lab_name" not in config:
        raise ValueError("Missing required field: lab_name")

    if "nodes" not in config:
        raise ValueError("Missing required field: nodes")

    if not isinstance(config["nodes"], dict) or not config["nodes"]:
        raise ValueError("The nodes field must be a non-empty dictionary.")

    for node_name, node_data in config["nodes"].items():
        if not isinstance(node_data, dict):
            raise ValueError(f"Node {node_name} must be a dictionary.")

        interfaces = node_data.get("interfaces")
        if not isinstance(interfaces, list) or not interfaces:
            raise ValueError(f"Node {node_name} must have at least one interface.")

        for index, interface in enumerate(interfaces):
            if not isinstance(interface, dict):
                raise ValueError(f"Node {node_name}, interface {index}, is invalid.")

            if "network" not in interface:
                raise ValueError(
                    f"Node {node_name}, interface {index}, has no network field."
                )


def _declared_protocols(frr: dict[str, Any], report: SemanticValidationReport, node: str) -> list[str]:
    protocols: list[str] = []

    if "protocol" in frr:
        protocols.append(str(frr["protocol"]).lower())

    raw_protocols = frr.get("protocols")
    if isinstance(raw_protocols, list):
        protocols.extend(str(protocol).lower() for protocol in raw_protocols)
    elif isinstance(raw_protocols, dict):
        for protocol, data in raw_protocols.items():
            if isinstance(data, dict) and data.get("enabled", True) is False:
                continue
            protocols.append(str(protocol).lower())
    elif raw_protocols is not None:
        report.error("frr_protocols_type", f"Node '{node}': frr.protocols must be a list or dictionary.")

    for protocol in SUPPORTED_FRR_PROTOCOLS:
        data = frr.get(protocol)
        if isinstance(data, dict) and data.get("enabled", True) is not False:
            protocols.append(protocol)

    daemons = frr.get("daemons")
    if isinstance(daemons, dict):
        daemon_protocols = {"ospfd": "ospf", "ripd": "rip", "bgpd": "bgp"}
        protocols.extend(
            protocol for daemon, protocol in daemon_protocols.items() if daemons.get(daemon)
        )

    result: list[str] = []
    for protocol in protocols:
        if protocol not in result:
            result.append(protocol)
        if protocol not in SUPPORTED_FRR_PROTOCOLS:
            report.error(
                "frr_protocol_unsupported",
                f"Node '{node}': unsupported FRR protocol '{protocol}'. "
                f"Supported protocols: {', '.join(sorted(SUPPORTED_FRR_PROTOCOLS))}.",
            )
    return [protocol for protocol in result if protocol in SUPPORTED_FRR_PROTOCOLS]


def _protocol_section(frr: dict[str, Any], protocol: str) -> dict[str, Any]:
    section: dict[str, Any] = {}
    raw_protocols = frr.get("protocols")
    if isinstance(raw_protocols, dict) and isinstance(raw_protocols.get(protocol), dict):
        section.update(raw_protocols[protocol])
    direct = frr.get(protocol)
    if isinstance(direct, dict):
        section.update(direct)
    return section


def _parse_asn(value: Any) -> int | None:
    try:
        asn = int(value)
    except (TypeError, ValueError):
        return None
    return asn if 1 <= asn <= 4_294_967_295 else None


def _iter_explicit_ospf_networks(frr: dict[str, Any]) -> Iterable[Any]:
    ospf = _protocol_section(frr, "ospf")
    if "networks" in ospf:
        networks = ospf["networks"]
    else:
        networks = frr.get("networks", [])
    if isinstance(networks, list):
        return networks
    if networks:
        return [networks]
    return []


def _target_references(config: dict[str, Any]) -> list[tuple[str, Any]]:
    references: list[tuple[str, Any]] = []
    node_keys = ("target_node", "target_server", "server_target")
    ip_keys = ("target_ip",)
    for key in node_keys + ip_keys:
        if key in config:
            references.append((key, config[key]))

    for section_name in ("connection_tests", "simulation", "attack", "dos"):
        section = config.get(section_name)
        if not isinstance(section, dict):
            continue
        for key in node_keys + ip_keys:
            if key in section:
                references.append((f"{section_name}.{key}", section[key]))
    return references


def _candidate_import_directories(config_path: Path) -> list[Path]:
    return [
        path
        for path in sorted(config_path.parent.iterdir(), key=lambda item: item.name.lower())
        if path.is_dir()
        and path.name != "__pycache__"
        and not path.name.startswith(".")
    ]


def validate_semantics(
    config: dict[str, Any],
    config_path: Path | None = None,
    import_dirs: bool = False,
) -> SemanticValidationReport:
    """Collect semantic errors and warnings without stopping at the first issue."""
    validate_config(config)
    nodes: dict[str, dict[str, Any]] = config["nodes"]
    report = SemanticValidationReport(node_count=len(nodes), import_dirs_checked=import_dirs)

    interfaces_by_node: dict[str, list[ipaddress.IPv4Interface | ipaddress.IPv6Interface]] = defaultdict(list)
    ip_owners: dict[str, list[tuple[str, int]]] = defaultdict(list)
    domains: dict[str, list[tuple[str, int, ipaddress.IPv4Interface | ipaddress.IPv6Interface | None]]] = defaultdict(list)

    for node_name, node_data in nodes.items():
        if not isinstance(node_name, str) or not NAME_PATTERN.fullmatch(node_name):
            report.error(
                "node_name_invalid",
                f"Invalid node name '{node_name}'. Use letters, numbers, '.', '_' or '-', without spaces.",
            )

        node_kind = str(node_data.get("type", "host")).lower()
        if node_kind not in SUPPORTED_NODE_TYPES:
            report.error(
                "node_type_unsupported",
                f"Node '{node_name}': unsupported type '{node_kind}'.",
            )

        interfaces = node_data["interfaces"]
        report.interface_count += len(interfaces)
        for index, interface_data in enumerate(interfaces):
            raw_domain = interface_data.get("network")
            domain = "" if raw_domain is None else str(raw_domain).strip()
            if not domain or not NAME_PATTERN.fullmatch(domain):
                report.error(
                    "network_name_invalid",
                    f"Node '{node_name}', interface {index}: invalid collision domain name '{domain}'.",
                )

            parsed_interface = None
            raw_ip = interface_data.get("ip")
            if raw_ip not in (None, ""):
                try:
                    parsed_interface = ipaddress.ip_interface(str(raw_ip))
                except ValueError:
                    report.error(
                        "invalid_ip",
                        f"Node '{node_name}', interface {index}: invalid IP address '{raw_ip}'.",
                    )
                else:
                    interfaces_by_node[node_name].append(parsed_interface)
                    ip_owners[str(parsed_interface.ip)].append((node_name, index))
                    if (
                        isinstance(parsed_interface, ipaddress.IPv4Interface)
                        and parsed_interface.network.prefixlen < 31
                        and parsed_interface.ip in {
                            parsed_interface.network.network_address,
                            parsed_interface.network.broadcast_address,
                        }
                    ):
                        report.error(
                            "invalid_host_ip",
                            f"Node '{node_name}', interface {index}: '{raw_ip}' is a network or broadcast address.",
                        )
            domains[domain].append((node_name, index, parsed_interface))

        if node_kind == "router":
            commands = "\n".join(str(command) for command in node_data.get("commands", []))
            if node_data.get("forwarding") is False or re.search(r"ip_forward\s*=\s*0", commands):
                report.error(
                    "router_forwarding_disabled",
                    f"Router '{node_name}' explicitly disables IPv4 forwarding.",
                )
            if len(interfaces) < 2:
                report.warn(
                    "router_single_interface",
                    f"Router '{node_name}' has only one interface and cannot forward between networks.",
                )

    report.collision_domain_count = len(domains)

    for address, owners in sorted(ip_owners.items()):
        if len(owners) > 1:
            locations = ", ".join(f"{node}:eth{index}" for node, index in owners)
            report.error("duplicate_ip", f"Duplicate IP address {address}: {locations}.")

    domain_subnets: dict[str, set[ipaddress.IPv4Network | ipaddress.IPv6Network]] = {}
    for domain, endpoints in sorted(domains.items()):
        if len(endpoints) == 1:
            node, index, _ = endpoints[0]
            report.warn(
                "isolated_network",
                f"Collision domain '{domain}' has a single endpoint ({node}:eth{index}) and is isolated.",
            )
        subnets = {interface.network for _, _, interface in endpoints if interface is not None}
        domain_subnets[domain] = subnets
        if len(subnets) > 1:
            report.error(
                "domain_subnet_inconsistent",
                f"Collision domain '{domain}' uses inconsistent subnets: "
                + ", ".join(str(network) for network in sorted(subnets, key=str))
                + ".",
            )

    domain_names = sorted(domain_subnets)
    for left_index, left_name in enumerate(domain_names):
        for right_name in domain_names[left_index + 1:]:
            overlaps = [
                (left, right)
                for left in domain_subnets[left_name]
                for right in domain_subnets[right_name]
                if left.version == right.version and left.overlaps(right)
            ]
            if overlaps:
                left, right = overlaps[0]
                report.error(
                    "subnet_overlap",
                    f"Collision domains '{left_name}' ({left}) and '{right_name}' ({right}) overlap.",
                )

    for node_name, node_data in nodes.items():
        gateway = node_data.get("default_gateway")
        if gateway not in (None, ""):
            try:
                gateway_ip = ipaddress.ip_address(str(gateway))
            except ValueError:
                report.error("gateway_invalid", f"Node '{node_name}': invalid default gateway '{gateway}'.")
            else:
                local_interfaces = interfaces_by_node.get(node_name, [])
                if not any(
                    interface.version == gateway_ip.version and gateway_ip in interface.network
                    for interface in local_interfaces
                ):
                    report.error(
                        "gateway_outside_subnet",
                        f"Node '{node_name}': gateway {gateway_ip} does not belong to any local interface subnet.",
                    )

        lowered_name = str(node_name).lower()
        requires_address = (
            "attacker" in lowered_name
            or lowered_name.startswith("zombie")
            or lowered_name == "server"
            or lowered_name.endswith("_server")
        )
        if requires_address and not interfaces_by_node.get(node_name):
            report.error(
                "role_without_ip",
                f"Node '{node_name}' requires at least one valid IP address for its role.",
            )

    protocols_by_node: dict[str, list[str]] = {}
    frr_by_node: dict[str, dict[str, Any]] = {}
    router_ids: dict[str, set[str]] = defaultdict(set)
    bgp_asn_by_node: dict[str, int] = {}

    for node_name, node_data in nodes.items():
        raw_frr = node_data.get("frr")
        if raw_frr in (None, False):
            continue
        if not isinstance(raw_frr, dict):
            report.error("frr_type", f"Node '{node_name}': frr must be a dictionary.")
            continue
        if raw_frr.get("enabled", True) is False:
            continue

        frr_by_node[node_name] = raw_frr
        protocols = _declared_protocols(raw_frr, report, node_name)
        protocols_by_node[node_name] = protocols
        if "ospf" in protocols:
            report.ospf_nodes += 1
        if "bgp" in protocols:
            report.bgp_nodes += 1

        effective_ids: set[str] = set()
        common_id = raw_frr.get("router_id")
        for protocol in protocols:
            section = _protocol_section(raw_frr, protocol)
            router_id = section.get("router_id", common_id)
            if router_id in (None, ""):
                continue
            try:
                parsed_router_id = ipaddress.IPv4Address(str(router_id))
            except ValueError:
                report.error(
                    "router_id_invalid",
                    f"Node '{node_name}': invalid {protocol.upper()} router ID '{router_id}'.",
                )
            else:
                effective_ids.add(str(parsed_router_id))
        for router_id in effective_ids:
            router_ids[router_id].add(str(node_name))

        if "bgp" in protocols:
            bgp = _protocol_section(raw_frr, "bgp")
            asn_value = bgp.get("asn", raw_frr.get("asn"))
            asn = _parse_asn(asn_value)
            if asn is None:
                report.error(
                    "bgp_asn_invalid",
                    f"Node '{node_name}' uses BGP but has no valid ASN (1-4294967295).",
                )
            else:
                bgp_asn_by_node[node_name] = asn

    for router_id, owners in sorted(router_ids.items()):
        if len(owners) > 1:
            report.error(
                "router_id_duplicate",
                f"Router ID {router_id} is duplicated by nodes: {', '.join(sorted(owners))}.",
            )

    for node_name, protocols in protocols_by_node.items():
        if "ospf" not in protocols:
            continue
        frr = frr_by_node[node_name]
        local_networks = [interface.network for interface in interfaces_by_node.get(node_name, [])]
        for interface_data in nodes[node_name]["interfaces"]:
            if "ospf_area" in interface_data and not interface_data.get("ip"):
                report.error(
                    "ospf_interface_without_ip",
                    f"Node '{node_name}': an OSPF-enabled interface has no IP address.",
                )
        for entry in _iter_explicit_ospf_networks(frr):
            raw_network = entry
            if isinstance(entry, dict):
                raw_network = entry.get("network", entry.get("prefix"))
            try:
                advertised = ipaddress.ip_network(str(raw_network), strict=False)
            except ValueError:
                report.error(
                    "ospf_network_invalid",
                    f"Node '{node_name}': invalid OSPF network '{raw_network}'.",
                )
                continue
            if not any(
                network.version == advertised.version and network.overlaps(advertised)
                for network in local_networks
            ):
                report.error(
                    "ospf_network_disconnected",
                    f"Node '{node_name}': OSPF network {advertised} is not connected to any interface.",
                )

    interface_ip_to_node = {
        str(interface.ip): node_name
        for node_name, interfaces in interfaces_by_node.items()
        for interface in interfaces
    }
    bgp_neighbors_by_node: dict[str, set[str]] = defaultdict(set)

    for node_name, protocols in protocols_by_node.items():
        if "bgp" not in protocols:
            continue
        frr = frr_by_node[node_name]
        bgp = _protocol_section(frr, "bgp")
        neighbors = bgp.get("neighbors", frr.get("neighbors", []))
        if not isinstance(neighbors, list):
            report.error("bgp_neighbors_type", f"Node '{node_name}': BGP neighbors must be a list.")
            continue
        for neighbor in neighbors:
            if not isinstance(neighbor, dict):
                report.error("bgp_neighbor_invalid", f"Node '{node_name}': invalid BGP neighbor entry.")
                continue
            raw_ip = neighbor.get("ip")
            try:
                neighbor_ip = str(ipaddress.ip_address(str(raw_ip)))
            except ValueError:
                report.error(
                    "bgp_neighbor_ip_invalid",
                    f"Node '{node_name}': invalid BGP neighbor IP '{raw_ip}'.",
                )
                continue
            bgp_neighbors_by_node[node_name].add(neighbor_ip)
            peer_node = interface_ip_to_node.get(neighbor_ip)
            if peer_node is None:
                report.error(
                    "bgp_neighbor_missing",
                    f"Node '{node_name}': BGP neighbor {neighbor_ip} does not belong to any declared node.",
                )
                continue
            if peer_node == node_name:
                report.error(
                    "bgp_neighbor_self",
                    f"Node '{node_name}': BGP neighbor {neighbor_ip} points to the node itself.",
                )
                continue

            remote_as = _parse_asn(neighbor.get("remote_as"))
            if remote_as is None:
                report.error(
                    "bgp_remote_as_invalid",
                    f"Node '{node_name}': neighbor {neighbor_ip} has an invalid remote_as.",
                )
                continue
            peer_asn = bgp_asn_by_node.get(peer_node)
            if peer_asn is None:
                report.error(
                    "bgp_peer_without_asn",
                    f"Node '{node_name}': neighbor {neighbor_ip} belongs to '{peer_node}', which has no valid BGP ASN.",
                )
            elif remote_as != peer_asn:
                report.error(
                    "bgp_remote_as_mismatch",
                    f"Node '{node_name}': neighbor {neighbor_ip} declares remote_as {remote_as}, "
                    f"but peer '{peer_node}' uses AS {peer_asn}.",
                )

    for node_name, neighbor_ips in bgp_neighbors_by_node.items():
        own_ips = {str(interface.ip) for interface in interfaces_by_node.get(node_name, [])}
        for neighbor_ip in neighbor_ips:
            peer_node = interface_ip_to_node.get(neighbor_ip)
            if not peer_node or peer_node == node_name or peer_node not in bgp_neighbors_by_node:
                continue
            if not (own_ips & bgp_neighbors_by_node[peer_node]):
                report.warn(
                    "bgp_neighbor_not_reciprocal",
                    f"BGP adjacency '{node_name}' -> '{peer_node}' is not declared in the reverse direction.",
                )

    declared_ips = set(interface_ip_to_node)
    target_references = _target_references(config)
    attack_topology = any(
        "attacker" in str(node_name).lower()
        or str(node_name).lower().startswith("zombie")
        for node_name in nodes
    )
    if attack_topology and not target_references:
        report.error(
            "target_missing",
            "An attacker/zombie topology must declare a target_node or target_ip.",
        )
    for section_name in ("connection_tests", "simulation", "attack", "dos"):
        section = config.get(section_name)
        if not isinstance(section, dict) or section.get("enabled", True) is False:
            continue
        if not any(location.startswith(f"{section_name}.") for location, _ in target_references):
            report.error(
                "target_missing",
                f"Section '{section_name}' is enabled but declares no target_node or target_ip.",
            )

    for location, target in target_references:
        if target in (None, ""):
            report.error("target_missing", f"'{location}' is empty.")
            continue
        if location.endswith("target_ip"):
            try:
                target_ip = str(ipaddress.ip_address(str(target)))
            except ValueError:
                report.error("target_ip_invalid", f"'{location}' contains invalid IP '{target}'.")
            else:
                if target_ip not in declared_ips:
                    report.error(
                        "target_ip_undeclared",
                        f"Target IP {target_ip} in '{location}' is not assigned to any node.",
                    )
        else:
            target_node = str(target)
            if target_node not in nodes:
                report.error(
                    "target_node_undeclared",
                    f"Target node '{target}' in '{location}' is not declared in nodes.",
                )
            elif not interfaces_by_node.get(target_node):
                report.error(
                    "target_node_without_ip",
                    f"Target node '{target_node}' in '{location}' has no valid IP address.",
                )

    connection_tests = config.get("connection_tests")
    if isinstance(connection_tests, dict):
        clients = connection_tests.get("clients", [])
        if isinstance(clients, list):
            for client in clients:
                if str(client) not in nodes:
                    report.error(
                        "client_node_undeclared",
                        f"Connection-test client '{client}' is not declared in nodes.",
                    )
        elif clients:
            report.error(
                "client_nodes_type",
                "'connection_tests.clients' must be a list of declared node names.",
            )

    if import_dirs:
        if config_path is None:
            report.error("import_config_path_missing", "Cannot validate imported directories without a YAML path.")
        else:
            invalid_dirs = [
                path.name
                for path in _candidate_import_directories(config_path)
                if path.name not in nodes
            ]
            if invalid_dirs:
                report.error(
                    "import_node_missing",
                    "Directories selected for import without a matching node: "
                    + ", ".join(invalid_dirs)
                    + ".",
                )

    return report


def semantic_report_lines(report: SemanticValidationReport) -> list[str]:
    lines = [
        f"[OK] {report.node_count} nodes validated",
        f"[OK] {report.collision_domain_count} collision domains validated",
    ]
    if not report.has_error("invalid_ip", "invalid_host_ip", "duplicate_ip"):
        lines.append("[OK] IP addresses are valid and unique")
    if not report.has_error("domain_subnet", "subnet_overlap", "gateway_"):
        lines.append("[OK] Subnets and default gateways are coherent")
    if not report.has_error("router_forwarding"):
        lines.append("[OK] Router forwarding declarations are coherent")
    if report.ospf_nodes and not report.has_error("ospf_", "router_id_"):
        lines.append(f"[OK] OSPF configuration is coherent on {report.ospf_nodes} nodes")
    if report.bgp_nodes and not report.has_error("bgp_", "router_id_"):
        lines.append(f"[OK] BGP configuration is coherent on {report.bgp_nodes} nodes")
    if report.import_dirs_checked and not report.has_error("import_"):
        lines.append("[OK] Imported directories match declared nodes")

    lines.extend(f"[{issue.severity}] {issue.message}" for issue in report.issues)
    if report.valid:
        lines.append(
            f"[OK] Semantic validation passed with {len(report.warnings)} warning(s)"
        )
    else:
        lines.append(
            f"[ERROR] Semantic validation failed with {len(report.errors)} error(s) "
            f"and {len(report.warnings)} warning(s)"
        )
    return lines


def print_semantic_report(report: SemanticValidationReport) -> None:
    for line in semantic_report_lines(report):
        print(line)


def parse_yaml_configuration(argument: str, config_dir: Path) -> tuple[Path, dict[str, Any]]:
    config_path = resolve_config_path(argument, config_dir)
    config = load_yaml(config_path)
    validate_config(config)
    return config_path, config
