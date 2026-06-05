# Thesis Project Technical Report

## 1. Introduction

The thesis project aims to design and develop a container-based network simulation environment for the automated creation of virtual labs and the experimental verification of the reachability of a networked application service.

The core idea is to use **Kathara** and **Docker** to emulate a network made up of hosts, routers, and servers. The topology is described through **YAML** configuration files, while a Python script automatically generates the files required to start the lab, including `lab.conf`, `.startup` files for each node, and routing configurations via **FRRouting**.

The project was designed as a foundation for progressive simulations. In a first phase, an attack-free environment is built in which multiple legitimate hosts connect to a service exposed by the server. In a subsequent phase, the same model can be extended with a separate lab that simulates a **Denial of Service** attack, comparing results obtained under normal conditions with those observed under service overload.

Keeping two separate labs — one without DoS and one with DoS — clearly separates the baseline phase from the attack phase. This makes experimental results easier to compare and allows network behavior to be analyzed in a more structured way.

---

## 2. General Project Architecture

The project structure is organized around the following main elements:

```text
Thesis/
├── configs/
│   ├── <namelab>.yml
│   
├── generate_lab.py
├── start.sh
├── stop.sh
├── del_lab.sh
├── server/
│   └── service.py
├── run_connection_tests.py
├── show_connection_results.py
└── labs/
    ├── <directory_labs_for_Katharà>
```

The `configs/` folder contains the network topology descriptions. Each YAML file represents a different lab, with nodes, interfaces, IP addresses, gateways, routing protocols, and lab metadata.

The `generate_lab.py` script reads a YAML file and automatically generates the corresponding Kathara lab. The `labs/` folder contains the generated labs, ready to be started.

The `server/service.py` file represents the application service exposed by the server. Client hosts connect to this service via `nc`, sending a payload and receiving a response.

The `run_connection_tests.py` and `show_connection_results.py` scripts are independent of the lab generator. The first runs the connection tests and produces logs and CSV files; the second reads the already-generated results and displays a concise summary.

---

## 3. Main Topology: Labs .yml


## 4. How `generate_lab.py` Works

The `generate_lab.py` script is the central component for automated lab generation.

Its purpose is to read a YAML file and produce a folder compatible with Kathara. The general command is:

```bash
python3 generate_lab.py configs/<name_file_lab>.yml --clean or ./generate_lab.py configs/<name_file_lab>.yml
```

Or, by specifying only the lab name if the file is inside `configs/`:

```bash
python3 generate_lab.py <name_file_lab> --clean or ./generate_lab.py <name_file_lab> --clean
```

The script performs the following operations:

1. reads the YAML file;
2. verifies the presence of required fields;
3. creates the lab folder inside `labs/`;
4. generates the `lab.conf` file;
5. generates a `.startup` file for each node;
6. generates FRR configurations for routers, switches, or firewalls;
7. optionally generates Wireshark support scripts.

### 4.1 YAML File Reading and Validation

The loading function reads the YAML file and returns a Python data structure. It checks that the file contains at least:

```yaml
lab_name: ...
nodes:
  ...
```

Each node must have at least one interface list. For each interface, the `network` field is mandatory, while the `ip` field is optional but typically required for hosts and routers.

Example:

```yaml
pc_a:
  type: host
  interfaces:
    - network: lan_a
      ip: 10.10.1.10/24
  default_gateway: 10.10.1.1
```

### 4.2 Generating `lab.conf`

The `lab.conf` file is the main file required by Kathara to describe the attachment of nodes to virtual networks.

Example of a generated line:

```text
pc_a[0]="lan_a"
```

For each node, the Docker image to use is also specified:

```text
pc_a[image]="kathara/base"
r1[image]="kathara/frr"
```

Hosts normally use the image:

```text
kathara/base
```

Routers, switches, and firewalls use:

```text
kathara/frr
```

### 4.3 Generating `.startup` Files

For each node, a file is created:

```text
<node>.startup
```

This file contains the commands that Kathara executes when the container starts.

For a host, the startup file typically contains:

```bash
ip address add 10.10.1.10/24 dev eth0

ip route add default via 10.10.1.1
```

For an FRR router, the following is also added:

```bash
systemctl start frr
```

This way, each node is automatically initialized with IP addresses, gateways, and — if needed — routing services.

### 4.4 Generating FRR Configurations

For routers, the following structure is created:

```text
<node>/etc/frr/
├── daemons
├── frr.conf
└── vtysh.conf
```

In the case of OSPF, the script automatically generates a configuration of the following type:

```text
router ospf
 ospf router-id 1.1.1.1
 network 10.10.1.0/24 area 0
 network 10.0.12.0/30 area 0
```

In the case of BGP, the script supports the configuration of:

```yaml
asn:
router_id:
networks:
neighbors:
```

and also inserts directives useful in educational labs, such as:

```text
no bgp ebgp-requires-policy
no bgp network import-check
```

These options prevent FRR from blocking eBGP route propagation in the absence of explicit policies — a common condition in educational environments.

---

## 5. How `service.py` Works

The file:

```text
server/service.py
```

implements a simple TCP service in Python.

The service listens on:

```python
HOST = "0.0.0.0"
PORT = 9000
```

When a client connects, the server sends a text menu:

```text
1) Ping
2) Server info
3) Quit
```

The client can send a choice. If it sends:

```text
1
```

the server responds:

```text
pong
```

This behavior is useful for testing because it verifies not only that the TCP port is open, but also that the application service responds correctly.

Manual example from a client:

```bash
printf "1\n" | nc 10.0.20.10 9000
```

Expected response:

```text
pong
```

The service must be started on the server node. It can be started manually by connecting to the container:

```bash
kathara connect server
python3 /server/service.py
```

or it can be called automatically in the server's `.startup` file, if the file is correctly copied inside the node's folder.

---

## 6. Lab Management Scripts

The project includes a number of support scripts to start, stop, or delete labs.

### 6.1 `start.sh`

The `start.sh` script takes the lab path as a parameter and starts Kathara.

Example:

```bash
./start.sh labs/Test1
```

Internally, the script enters the lab folder and runs:

```bash
kathara lstart
```

### 6.2 `stop.sh`

The `stop.sh` script takes the lab path as a parameter and stops the Kathara environment.

Example:

```bash
./stop.sh labs/Test1
```

Internally it runs:

```bash
kathara lclean
```

If present, it can also invoke Wireshark shutdown scripts.

### 6.3 `del_lab.sh`

The `del_lab.sh` script is used to delete generated labs.

Example:

```bash
./del_lab.sh labs/Test1
```

Or, to delete all labs:

```bash
./del_lab.sh all
```

---

## 7. Connection Test Scripts

The experimental part of the project is based on two standalone scripts:

```text
run_connection_tests.py
show_connection_results.py
```

These scripts do not need to be generated by `generate_lab.py`. They should be copied into the project folder or the lab folder and run after the simulation has started.

The separation is as follows:

```text
run_connection_tests.py
    runs the tests
    uses nc inside client containers
    generates CSV, logs, and full responses

show_connection_results.py
    reads already-generated CSV files
    does not run new tests
    displays a concise summary
```

This separation is correct because it isolates the data-collection phase from the results-analysis phase.

---

## 8. How `run_connection_tests.py` Works

The `run_connection_tests.py` script runs connection tests via `nc` from client containers to a target specified by the user.

The basic command is:

```bash
./run_connection_tests.py
```

The script interactively prompts for:

```text
Server target IP/host
TCP service port
Kathara clients to use
Scenario
Attempts per client
Timeout
Delay between attempts
Payload to send
Expected text in response
```

Example configuration:

```text
Server target IP/host: 10.0.20.10
TCP service port: 9000
Kathara clients to use: pc_a pc_b pc_c pc_d
Scenario: baseline
Attempts per client: 10
Timeout (seconds): 1
Delay between attempts: 1
Payload to send: 1\n
Expected text in response: pong
```

The script identifies active Kathara containers associated with the clients. If automatic detection is implemented, the script can recognize nodes with names in the format:

```text
pc_<letter_or_number>
```

For example, from a Docker container named:

```text
kathara_luke-ruyxex5uc4qjndb0lle9q_pc_b_8vR0uwt29JHsNXyfiSwNEQ
```

the script extracts the node name:

```text
pc_b
```

For each client and each attempt, the script runs a connection to the server:

```bash
printf "1\n" | nc 10.0.20.10 9000
```

The response is saved to a file and checked for the expected text, for example:

```text
pong
```

If the expected text is present, the attempt is considered successful. Otherwise, the attempt is recorded as an error.

### 8.1 Output Generated by `run_connection_tests.py`

The script generates a `results/` folder and a `logs/` folder.

Example:

```text
results/
├── connection_results_baseline_20260605_120203.csv
└── responses_baseline_20260605_120203/
    ├── pc_a_attempt_1.txt
    ├── pc_a_attempt_2.txt
    ├── pc_b_attempt_1.txt
    └── ...

logs/
├── connection_tests_baseline_20260605_120203.log
├── pc_a_baseline_20260605_120203.log
├── pc_b_baseline_20260605_120203.log
└── ...
```

The CSV contains one row per connection attempt. The main columns are:

```text
timestamp
scenario
client
attempt
target
port
status
elapsed_ms
error
response_file
```

Example:

```csv
timestamp,scenario,client,attempt,target,port,status,elapsed_ms,error,response_file
2026-06-05T12:02:03+02:00,baseline,pc_a,1,10.0.20.10,9000,OK,52.73,,results/responses_baseline_20260605_120203/pc_a_attempt_1.txt
```

---

## 9. How `show_connection_results.py` Works

The `show_connection_results.py` script reads the results generated by `run_connection_tests.py` and displays a concise report.

Basic command:

```bash
./show_connection_results.py
```

If no specific file is specified, the script automatically looks for the most recent CSV in the:

```text
results/
```

folder. A specific CSV can also be provided:

```bash
./show_connection_results.py results/connection_results_baseline_20260605_120203.csv
```

The report displays:

```text
Scenario
Target
Clients tested
Total requests
Completed requests
Failed requests
Average response time (ms)
Client errors
Per-client summary
Error details
```

Example output:

```text
Connection Summary Report
Scenario: baseline
Target: 10.0.20.10:9000
Clients tested: pc_a, pc_b, pc_c, pc_d

General Metrics
Metric                              Value
------------------------------------------------------
Total requests                      40
Completed requests                  40
Failed requests                     0
Average response time (ms)          52.85
Client errors                       0

Per-Client Summary
Client          Requests   OK       Fail     Avg ms
----------------------------------------------------------
pc_a            10         10       0        52.73
pc_b            10         10       0        53.28
pc_c            10         10       0        52.53
pc_d            10         10       0        53.01

Client Error Details
no errors detected
```

This script is useful for concisely documenting simulation results and comparing multiple scenarios.

---

## 10. How to Run a Generic Simulation

The complete procedure to run a simulation is as follows.

### 10.1 Environment Setup

First, the following must be installed:

```text
Docker
Kathara
Python 3
PyYAML
```

The main Python dependency is:

```bash
pip install pyyaml
```

### 10.2 Lab Generation

From the main project directory:

```bash
python3 generate_lab.py configs/Test1.yml --clean or ./generate_lab.py configs/Test1.yml --clean 
```

The `--clean` flag deletes any previous generation of the same lab and recreates the folder from scratch.

The lab will be generated in:

```text
labs/Test1/
```

### 10.3 Starting the Lab

Navigate to the lab folder:

```bash
cd labs/Test1
```

Start Kathara:

```bash
kathara lstart
```

Alternatively, from the project root:

```bash
./start.sh labs/Test1
```

### 10.4 Starting the Service

The Python service must be running on the server node.

Start the service in the server by terminal:

```bash
python3 service.py
```

If the file is located at a different path inside the lab, adjust the command accordingly. What matters is that the service is listening on the configured port, for example:

```text
10.0.20.10:9000
```

To verify manually from a client:

```bash
printf "1\n" | nc 10.0.20.10 9000
```

If the service is working, the client receives:

```text
pong
```

### 10.5 Running Automated Tests

From the lab folder, run:

```bash
./run_connection_tests.py
```

Enter the required parameters:

```text
Server target IP/host: 10.0.20.10
TCP service port: 9000
Kathara clients to use: pc_a pc_b pc_c pc_d
Scenario: baseline
Attempts per client: 10
Timeout (seconds): 1
Delay between attempts: 1
Payload to send: 1\n
Expected text in response: pong
```

The script automatically runs the tests and saves the results.

### 10.6 Viewing Results

After the tests complete:

```bash
./show_connection_results.py
```

The script will display a concise summary of the connections made.

### 10.7 Stopping the Lab

To stop the lab:

```bash
kathara lclean
```

Or from the project root:

```bash
./stop.sh labs/Test1
```

---

## 11. Baseline Simulation and Future DoS Simulation

The current simulation represents the baseline scenario — the normal system behavior in the absence of an attack.

In this phase:

```text
clients connect to the service;
the server responds correctly;
response times and errors are measured;
results are saved to CSV.
```

The next phase involves building a second lab, separate from the first, dedicated to the DoS scenario.

The recommended approach is to have two separate YAML files:

```text
configs/Test1_baseline.yml
configs/Test1_dos.yml
```

The baseline lab will contain only legitimate clients and the server.

The DoS lab will include an attacker node or an anomalous traffic generation script. The service will remain the same, making the results comparable.

The experimental logic will be:

```text
1. run the baseline test;
2. save CSV and logs;
3. run the test in the DoS scenario;
4. save CSV and logs;
5. compare completed requests, failed requests, and average response times.
```

This will allow the impact of the attack on service availability to be demonstrated.

---

## 12. Expected Results

In the baseline lab, the expected result is:

```text
Total requests: 40
Completed requests: 40
Failed requests: 0
Average response time (ms): stable
Client errors: 0
```

In the DoS lab, a degradation of metrics is expected:

```text
increase in average response time;
increase in failed requests;
possible timeouts;
reduction in service availability.
```

The comparison between the two scenarios forms the experimental core of the thesis.

---

## 13. Conclusion

The project implements a framework for generating and running containerized network labs. Topology configuration is handled via YAML, while the automatic generation of Kathara files is managed by a Python script.

The presence of FRR routers enables the simulation of realistic networks with dynamic routing protocols such as OSPF and BGP. The Python service exposed in the DMZ allows application-level connectivity to be verified from the clients. The test scripts collect repeatable and measurable results, producing CSV files, logs, and concise summaries.

The architecture is well-suited for the subsequent introduction of DoS scenarios, keeping the baseline lab and the lab under attack separate. This separation enables a clear and well-documented comparison of service behavior under normal conditions versus overload conditions.

The work therefore provides a concrete foundation for an experimental thesis on network emulation, automated generation of virtual environments, and analysis of the impact of availability attacks on network services.

