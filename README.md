# Technical Report – Kathara Network Lab Generator

## 1. Project Overview

This project provides a Python-based framework for automatically generating Kathara network laboratories from YAML configuration files.

The main goal is to describe a network topology in a structured and readable format, then automatically generate a complete Kathara lab containing:

- `lab.conf`;
- startup files for each device;
- FRRouting configuration files;
- optional Wireshark integration;
- optional file import into hosts;
- connection testing tools;
- controlled DoS simulation support.

The project is designed for educational, experimental, and validation purposes. It allows repeatable testing of different network topologies without manually writing all Kathara configuration files.

The general project structure is:

```text
Thesis/
├── configs/                       # YAML topology files and optional host files
├── src_gen_lab/                   # Core generator modules
│   ├── Lexer.py
│   ├── Parser.py
│   └── gen_lab.py
├── generate_lab.py                # Main lab generation wrapper
├── start.sh                       # Lab startup script
├── stop.sh                        # Lab shutdown script
├── del_lab.sh                     # Generated lab removal script
├── run_connection_tests.py        # TCP connection test script
├── show_connection_results.py     # Result analysis and RTT plot script
├── attacks/                       # Controlled DoS-related scripts
├── labs/                          # Generated Kathara labs
├── logs/                          # Test logs
└── results/                       # CSV results and generated plots
```

## Requirements

- Python 3.10+ (or compatible)
- PyYAML (`pip install pyyaml`)
- Kathara installed and configured
- Docker available for Wireshark and lab containers

## Main usage

Generate a lab by passing the YAML file or the lab name located in `configs/`:

```bash
./generate_lab.sh configs/client-router-server.yml
```

Or:

```bash
./generate_lab.sh client-router-server --clean
```

Supported options:

- `--config-dir DIR`: YAML search directory (default `configs`)
- `--output-dir DIR`: output directory for generated labs (default `labs`)
- `--clean`: delete and recreate the lab directory
- `--force`: overwrite existing files
- `--sniff-node NODE`: generate Wireshark scripts for a specific node
- `--wireshark {ask,enabled,disabled}`: Wireshark generation mode

## Generator architecture details

### YAML Topology Description

Each lab is defined through a YAML file stored inside the configs/ directory.

A basic YAML topology contains:

```yaml
lab_name: lab_name
nodes: # defines the list of nodes in the simulation
  node1:  # name of the node
    type: host  # type of the devise, it can be "host" or "router"
    interfaces:
      - network: lan_a # name of the network
        ip: 10.10.1.10/24 # ipv4 address
    default_gateway: 10.10.1.1 # default gateway's IPv4 
```

This generates a Kathara .startup file equal to:
```
ip address add 10.10.1.10/24 dev eth0
ip route add default via 10.10.1.1
```

Example of a router:
```yaml
r1: # router's name
  type: router # define the node as a router
  interfaces: # configure interfaces
    - network: lan_a # name of the first network
      ip: 10.10.1.1/24 # ip addr of the network interface (this will be the eth0)
    - network: r1_r2 # name of the second network
      ip: 10.0.12.1/30 # ip addr of the network interface (this will be the eth1)
  frr: # configuration of frr 
    enabled: true # implement the zebra's daemon
    protocol: ospf # define wich daemon put on (can be multiples daemons --> es. protocol: [ospf, bgp])
    router_id: 1.1.1.1 # configure the daemon 
    area: 0
```



Each node requires:

- `interfaces`: a list of interfaces
- for each interface: `network`

Common options:

- `type`: `host`, `router`, `switch`, `firewall`
- `ip`: IP address with prefix
- `default_gateway`
- `routes`: static routes
- `commands`: custom commands in the `.startup` file
- `image`: custom Docker image
- `frr`: FRR-specific configuration

## Generated output

For each generated lab:

- `labs/<lab_name>/lab.conf`
- `labs/<lab_name>/<node>.startup`
- `labs/<lab_name>/<node>/etc/frr/daemons`
- `labs/<lab_name>/<node>/etc/frr/frr.conf`
- `labs/<lab_name>/<node>/etc/frr/vtysh.conf`
- optional Wireshark scripts: `start_wireshark.sh`, `sniff.sh`, `stop_wireshark.sh`

## FRR support

For FRRouting devices, the generator automatically creates:

- `daemons`
- `frr.conf`
- `vtysh.conf`

FRR can be configured in YAML with sections such as:

```yaml
frr:
  protocol: ospf
  router_id: 1.1.1.1
  area: 0
  networks:
    - 10.10.1.0/24
```

For BGP:

```yaml
frr:
  asn: 65001
  router_id: 1.1.1.1
  neighbors:
    - ip: 10.0.12.2
      remote_as: 65002
  networks:
    - 10.10.1.0/24
```

## Application service

The server code is in `server/service.py`. The service listens on `0.0.0.0:9000` and responds to simple test requests, useful for verifying end-to-end connectivity in the lab.

## Connection tests

- `run_connection_tests.py`: runs connectivity tests against the lab service.
- `show_connection_results.py`: displays a summary of the test results.

## Notes

- Make sure Kathara is installed before running generated labs.
- `generate_lab.sh` is the recommended convenience script for launching the generator.
