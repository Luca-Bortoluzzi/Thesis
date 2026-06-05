# Thesis Project Technical Report

## Overview

This project generates Kathara virtual labs from YAML configuration files. The generator creates the necessary structure to launch network topologies with hosts, routers, switches, and firewalls, including `lab.conf`, node `.startup` files, and FRR configurations.

The goal is to support experiments on containerized networks with declaratively defined topologies, making it easy to generate and run educational and test labs.

## Project structure

```text
Thesis/
├── configs/                 # YAML files describing topologies
├── gen_lab/                 # Python package that generates labs
│   ├── __init__.py
│   ├── Lexer.py             # YAML loading and config path resolution
│   ├── Parser.py            # YAML syntax validation
│   ├── gen_lab.py           # Lab generation logic
│   └── generate_lab.py      # Package entrypoint
├── generate_lab.sh          # Convenience script to generate labs
├── start.sh                 # Lab startup script for Kathara
├── stop.sh                  # Lab shutdown script
├── del_lab.sh               # Script to remove generated labs
├── server/                  # Application service used in tests
│   └── service.py
├── run_connection_tests.py  # Runs connectivity tests against lab services
├── show_connection_results.py # Displays test results summary
├── labs/                    # Generated labs ready for Kathara
├── logs/
└── results/
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

### `generate_lab.sh`

A small wrapper script that invokes the Python `gen_lab` package with the provided arguments.

### `gen_lab/` package

- `Lexer.py`: loads the YAML file, verifies it is valid, and resolves the configuration path.
- `Parser.py`: validates the YAML structure with required field and type checks.
- `gen_lab.py`: generates `lab.conf`, node `.startup` files, FRR configurations, and optional Wireshark scripts.
- `generate_lab.py`: package entrypoint used by `generate_lab.sh`.

### Generation workflow

1. The YAML loader reads the file and returns the parsed data.
2. The parser validates `lab_name`, `nodes`, `interfaces`, and `network` fields.
3. The lab directory is created at `labs/<lab_name>`.
4. `lab.conf` is generated with node interfaces and images.
5. Each node receives a `<node>.startup` file.
6. Routers/switches/firewalls receive FRR configuration under `<node>/etc/frr`.
7. Optional Wireshark helper scripts are generated if requested.

## Supported YAML format

The YAML file must include at least:

```yaml
lab_name: lab_name
nodes:
  node1:
    type: host
    interfaces:
      - network: lan_a
        ip: 10.10.1.10/24
    default_gateway: 10.10.1.1
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
