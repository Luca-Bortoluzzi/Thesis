# Kathara Network Lab Generator and DoS Measurement Framework

This project generates Kathara network laboratories from YAML topology files and
provides a controlled, repeatable workflow for measuring service availability
under normal traffic and bounded denial-of-service (DoS) load.

It is intended for educational, experimental, and thesis work in isolated
environments. The included DoS tooling must only be used inside laboratories
that you own or are explicitly authorized to operate.

## Capabilities

- Generate Kathara labs from YAML topology descriptions.
- Validate topology semantics before writing a laboratory.
- Create host, router, switch, and firewall startup configuration.
- Generate FRRouting configuration for OSPF, RIP, and BGP.
- Import per-node files into generated labs.
- Optionally attach Wireshark using Kathara real-time integration.
- Measure ICMP RTT, TCP connection time, application response time, and request
  completion time from inside client containers.
- Run baseline and controlled DoS scenarios with concurrent legitimate clients.
- Produce CSV data, textual reports, and metric plots.

## Repository layout

    Thesis/
    ├── configs/                    YAML topologies and files imported into nodes
    │   ├── dos_lab.yml             Controlled DoS topology
    │   ├── mitigation.yml          Reverse-proxy mitigation topology
    │   ├── server/service.py       Measured TCP service
    │   ├── server/reverse_proxy.py Rate-limiting TCP reverse proxy
    │   ├── backend_server/         Private application service for mitigation
    │   ├── attacker/               C2 controller and related assets
    │   └── zombie_*/               Zombie agents
    ├── src_gen_lab/                YAML parser and Kathara lab generator
    ├── labs/                       Generated labs
    ├── attacks/                    Standalone controlled-attack utilities
    ├── run_connection_tests.py     Client-side metric collector
    ├── show_connection_results.py  CSV report and plotting tool
    ├── simulate.sh                 End-to-end baseline/attack workflow
    ├── zombies_start.py            Starts zombie agents in an existing lab
    ├── zombies_stop.py             Stops compatible zombie agents
    ├── results/                    CSV results and plots
    └── logs/                       Per-run and per-client logs

## Requirements

- Python 3.10 or newer.
- PyYAML.
- matplotlib for plot generation.
- Bash, timeout, and standard Unix utilities.
- Docker.
- Kathara, configured for the current user.
- python3 and ping in the Kathara client image used for measurements.

Install Python dependencies:

    python3 -m pip install pyyaml matplotlib

## Generate a lab

Generate from a YAML path:

    ./generate_lab.py configs/client-router-server.yml

Generate by the configuration name in configs:

    ./generate_lab.py client-router-server --clean

Validate syntax and semantics without creating or changing a lab:

    ./generate_lab.py dos_lab --validate-only

Generate the DoS lab and import the server, attacker, and zombie directories:

    ./generate_lab.py dos_lab --force --import-dirs --zombies manual \
      --zombies-node attacker \
      --zombies-ips "10.20.1.11,10.20.1.12,10.20.1.13"

Important generator options:

- --config-dir DIR: directory containing topology YAML files; default configs.
- --output-dir DIR: destination directory; default labs.
- --clean: remove and recreate the selected lab directory.
- --force: overwrite generated files.
- --validate-only: validate the YAML without writing files or prompting.
- --import-dirs: import node directories next to the YAML file; every visible
  directory must have a matching node in that topology.
- --wireshark ask|enabled|disabled: configure real-time Wireshark integration.
- --sniff-node NODE: attach Wireshark to the networks of a node.
- --zombies manual|auto|disabled: manage zombies.txt generation.
- --zombies-node NODE: node that receives zombies.txt.
- --zombies-ips LIST: comma-separated zombie IP addresses.

## Semantic YAML validation

Semantic validation runs both with --validate-only and automatically before
normal generation. Errors stop generation, while warnings identify suspicious
but potentially intentional topology choices.

The validator checks:

- valid, unique interface addresses and non-overlapping collision-domain
  subnets;
- default gateways belonging to a local interface subnet;
- valid node and collision-domain names, isolated single-ended networks, and
  explicitly disabled router forwarding;
- unique and valid FRR router IDs and supported OSPF, RIP, and BGP protocols;
- OSPF networks connected to local interfaces;
- existing BGP neighbor addresses, coherent remote_as values, and reciprocal
  neighbor declarations;
- IP addresses for attacker, zombie, server, and configured target nodes;
- declared connection-test clients and targets;
- when --import-dirs is used, a matching YAML node for every imported folder.

Because --import-dirs selects every visible directory beside the YAML file, a
shared configuration directory must not contain folders belonging exclusively
to another topology. The validator reports those folders before generation.

The router node type implies forwarding in generated Kathara router images;
forwarding: false or a command that sets ip_forward=0 is treated as an error.
Topologies containing attacker or zombie nodes must also declare their target:

    simulation:
      enabled: true
      target_node: server

Example output:

    [OK] 18 nodes validated
    [OK] 11 collision domains validated
    [OK] IP addresses are valid and unique
    [OK] OSPF configuration is coherent on 5 nodes
    [OK] Semantic validation passed with 0 warning(s)

## YAML topology format

Every configuration contains a lab_name and a nodes mapping. Each node must
define at least one interface with a network.

    lab_name: example_lab
    description: Example routed network
    nodes:
      client:
        type: host
        interfaces:
          - network: lan
            ip: 10.0.1.10/24
        default_gateway: 10.0.1.1

      router:
        type: router
        interfaces:
          - network: lan
            ip: 10.0.1.1/24
          - network: transit
            ip: 10.0.12.1/30
        frr:
          enabled: true
          protocol: ospf
          router_id: 1.1.1.1
          area: 0

Supported node properties:

- type: host, router, switch, or firewall.
- image: optional Docker image override.
- interfaces: list of network interfaces with network and optional ip.
- default_gateway: default IPv4 gateway.
- routes: static routes.
- commands: commands written to the startup file.
- frr: routing configuration for FRR-capable nodes.

Supported FRR protocol forms:

- frr.protocol: a single protocol such as ospf, rip, or bgp.
- frr.protocols: a list or mapping of protocol definitions.
- frr.daemons: direct daemon enablement.

The generator writes lab.conf, node startup files, and FRR files where needed.

## Reverse-proxy mitigation lab

The mitigation topology is directly comparable with dos_lab: it uses the same
eight legitimate clients, three zombies, attacker network, routers, and OSPF
links. The public service address belongs to a reverse proxy instead of the
application server.

    clients / zombies -> server 10.30.1.10:9000 -> backend_server 10.30.2.10:9000

The public server node runs the reverse proxy and has two interfaces. Its public interface is advertised through the
enterprise routing domain, while the backend interface belongs to a private
network that is not connected to or advertised by the routing core. Therefore,
the zombies cannot bypass the proxy and connect directly to the server.

The proxy allows at most two simultaneous connections from one source IP. The
three zombies can consequently occupy no more than six backend handlers in
total, leaving capacity for legitimate hosts. Excess connections receive
RATE_LIMITED and are closed before a backend connection is opened.

Generate the laboratory:

    ./generate_lab.py mitigation --force --import-dirs --wireshark disabled

Run baseline and protected-attack measurements:

    ./simulate.sh mitigation full --plot

The same command is available through Make:

    make mitigation

For a lab named mitigation, the measurement and attack target remains server.
simulate.sh automatically uses backend_server only for starting and checking
the private application service. An explicit --server option overrides this
selection. Results retain the standard baseline and dos scenario names.

Reverse-proxy environment variables:

- PROXY_LISTEN_HOST and PROXY_LISTEN_PORT: public listener; defaults 0.0.0.0
  and 9000.
- PROXY_BACKEND_HOST and PROXY_BACKEND_PORT: private application endpoint;
  defaults 10.30.2.10 and 9000.
- PROXY_MAX_PER_SOURCE: simultaneous connections allowed per source; default 2.
- PROXY_MAX_ACTIVE: global proxy connection limit; default 32.
- PROXY_CONNECT_TIMEOUT: backend connection timeout; default 2 seconds.
- PROXY_IDLE_TIMEOUT: maximum idle relay time; default 12 seconds.

## Controlled DoS simulation

simulate.sh coordinates the full experiment:

1. Starts the selected Kathara lab.
2. Starts the TCP service.
3. Runs the baseline measurement.
4. Starts zombie agents.
5. Starts the DoS measurement.
6. Sends the zombie attack command after a configurable delay, while legitimate
   client rounds are already running.
7. Samples the server service plus container CPU and memory metrics.
8. Prints the reports and cleans the lab unless requested otherwise.

Run the full workflow:

    ./simulate.sh dos_lab full

Run only the baseline:

    ./simulate.sh dos_lab baseline --plot

Run only the attack phase in an already running lab:

    ./simulate.sh dos_lab attack --no-start --plot

Useful simulation options:

- --target NODE_OR_IP: target service; default server.
- --server NODE: node running service.py; default server.
- --port N: TCP service port; default 9000.
- --clients "pc_a1 pc_a2": explicit client list; default all pc_* nodes.
- --attempts N: number of simultaneous rounds per client; default 10.
- --timeout N: timeout of an individual measurement in seconds; default 3.
- --delay N: pause between rounds in seconds; default 1.
- --startup-wait N: wait after starting the Kathara lab; default 25.
- --attack-connections N: controlled connection workers per zombie; default 40.
- --attack-duration N: bounded attack duration in seconds; default 60, maximum 90.
- --attack-start-delay N: delay after DoS measurements start before the C2
  command is sent; default 1.
- --connect-timeout N: maximum duration of one kathara exec command; default 60.
- --connect-retries N: number of kathara exec attempts; default 3.
- --plot: generate metric plots.
- --keep-running: leave the Kathara lab active after the workflow.
- --dry-run: validate generated remote scripts and print the planned workflow.

The historical positional mode dos remains an alias for attack.

Advanced parameters can be supplied through environment variables:

    SIM_SERVER
    SIM_PORT
    SIM_TIMEOUT
    SIM_DELAY
    SIM_STARTUP_WAIT
    SIM_ATTACK_START_DELAY
    SIM_CONNECT_TIMEOUT
    SIM_CONNECT_RETRIES
    SIM_ROUTING_RETRIES

## Attack timing and load model

The DoS phase intentionally starts the legitimate measurement process first.
After ATTACK_START_DELAY seconds, the attacker sends an ATTACK command to the
zombies. This creates a short pre-attack reference at the beginning of the DoS
plot and makes the transition visible by round.

Each zombie worker keeps a TCP connection idle for a bounded cycle and
reconnects before the server-side application timeout. This keeps pressure on
the service for the configured attack duration rather than ending after the
first server timeout.

The included server limits concurrent application handlers. Under saturation it
may reply BUSY, close a connection, or fail to produce pong. Therefore, the
most meaningful DoS metric is application availability, not necessarily ICMP
RTT or TCP connection time.

## Server-side metrics

The TCP service writes a timestamped sample every second and records additional
rows whenever a connection is accepted, rejected, answered with BUSY, or
closed. simulate.sh labels each row with the active scenario so that server
load can be aligned directly with client latency, availability, and timeout
measurements.

Each simulation stores two additional files in its laboratory result directory:

- results/<lab>/simulation_<NNNN>/server_metrics.csv: active, accepted,
  rejected, completed and
  maximum simultaneous connections, BUSY responses, average connection
  duration, and active threads.
- results/<lab>/simulation_<NNNN>/server_container_metrics.csv: Docker CPU,
  memory usage, memory percentage, and PID count sampled during baseline and
  DoS measurements.

At the end of a run, show_server_metrics.py prints both the final counters and
a baseline/DoS correlation table. It can also be run manually:

    python3 show_server_metrics.py results/dos_lab/simulation_0001/server_metrics.csv \
      --container results/dos_lab/simulation_0001/server_container_metrics.csv


## Measurement methodology

run_connection_tests.py runs a small Python measurement program inside each
Kathara client container. The measured values therefore exclude Docker command
startup and host shell overhead.

Each CSV row contains:

- request_index: simultaneous request round index, not a packet number.
- icmp_rtt_ms: ICMP echo RTT measured by ping inside the client.
- tcp_connect_ms: TCP connect/handshake duration measured by the client socket.
- application_response_ms: time from sending the application request to
  receiving pong.
- request_completion_ms: connection plus application completion time.
- application_status and application_error: outcome of the pong request.
- host_command_completion_ms: diagnostic host orchestration time, including
  docker exec and the complete measurement command. It is not a network RTT.
- experiment_elapsed_ms: elapsed experiment time used for effective duration.

Run measurements manually:

    ./run_connection_tests.py --lab dos_lab --target server --port 9000 \
      --clients "pc_a1 pc_a2 pc_b1 pc_b2" \
      --scenario baseline --attempts 20 --timeout 3 --delay 1 \
      --non-interactive

The next simulation number for the selected lab is assigned automatically.
Use --simulation N to add another scenario to a specific simulation.

Interactive mode is available when --non-interactive is omitted.

## Results and plots

Results are written to:

    results/<lab>/simulation_<NNNN>/connection_results_<scenario>.csv
    logs/<lab>/simulation_<NNNN>/connection_tests_<scenario>.log
    logs/<lab>/<host>/simulation_<NNNN>/<scenario>.log

A complete simulate.sh run allocates one number and uses it for baseline, DoS,
server metrics, and container metrics. A number can also be selected explicitly:

    ./simulate.sh dos_lab full --simulation 7

Display a report:

    ./show_connection_results.py \
      results/dos_lab/simulation_0001/connection_results_baseline.csv

Generate plots:

    ./show_connection_results.py \
      results/dos_lab/simulation_0001/connection_results_dos.csv --plot

The report includes:

- total, successful, and failed application requests;
- application availability percentage;
- timeout counts for ICMP, TCP connection, and application response;
- effective experiment duration;
- mean, minimum, P50, P95, and maximum for every metric;
- per-client application statistics;
- error breakdown by measurement stage.

The current plot contains four panels:

1. ICMP RTT by request index and client.
2. TCP connection time by request index and client.
3. Application response time for completed requests.
4. Application availability by request round.

The fourth panel is essential for DoS analysis. A saturated service can reject
requests immediately, leaving successful latency samples low while availability
falls sharply.

Older CSV files with elapsed_ms remain readable. They are labeled as legacy
end-to-end measurements because they include docker exec, shell, and netcat
overhead; they must not be interpreted as network RTT values.

## Service protocol

configs/server/service.py implements the reference TCP service. It listens on
port 9000 by default, displays a small menu, and returns pong when it receives
the selection 1. The measurement client sends this selection and only records
an application success when pong is received.

The service supports the following environment variables:

- SERVICE_PORT: listen port; default 9000.
- SERVICE_BACKLOG: TCP accept backlog; default 40.
- SERVICE_MAX_ACTIVE: maximum concurrent application handlers; default 20.
- SERVICE_CLIENT_TIMEOUT: timeout while waiting for a client selection; default
  10 seconds.
- SERVICE_METRICS_PATH: server CSV path; default /shared/server_metrics.csv.
- SERVICE_METRICS_SCENARIO_PATH: file containing the current scenario label;
  default /shared/server_metrics_scenario.
- SERVICE_METRICS_INTERVAL: periodic sample interval in seconds; default 1.

## Troubleshooting

- Ensure Kathara and Docker are available before starting a simulation.
- If kathara exec is slow after startup, increase SIM_CONNECT_RETRIES or
  SIM_CONNECT_TIMEOUT.
- If a plot cannot be generated, install matplotlib in the Python environment
  running show_connection_results.py.
- If ping is not available in a client image, ICMP rows report
  ping_not_available while TCP and application measurements can still run.
- The generated remote scripts are checked with sh -n before they are executed
  in a container. A syntax error is reported before the related kathara exec
  command is attempted.

## Validation

Run the built-in tests:

    python3 -m unittest discover -s tests -v

Validate the simulation workflow without starting containers:

    ./simulate.sh dos_lab full --dry-run --plot
