# Controlled DoS lab scripts

Scripts for a bounded, thesis-oriented DoS availability experiment in a Kathara lab.

Default scenario:

- target: `10.40.10.10:9000`
- attacker nodes: `pc_e`, `pc_f`
- traffic model: controlled TCP connection flood
- safety caps: max 120 seconds, max 100 workers per attacker, private/lab IP guard inside `dos_client.py`

## Optional YAML section

```yaml
dos_tests:
  enabled: true
  scenario: controlled_tcp_connection_flood
  attackers:
    - pc_e
    - pc_f
  target_node: server
  target_ip: 10.40.10.10
  port: 9000
  duration: 30
  workers_per_attacker: 20
  connection_timeout: 1
  payload_size: 64
  delay_between_connections_ms: 20
  mode: connect-flood
```

## Usage

```bash
kathara lstart
python3 attacks/run_dos_attack.py configs/Test_enterprise_multi_as_structured.yml
python3 attacks/collect_dos_logs.py --attackers pc_e pc_f --out results/dos_summary.json
bash attacks/stop_dos_attack.sh configs/Test_enterprise_multi_as_structured.yml pc_e pc_f
```

Override intensity:

```bash
python3 attacks/run_dos_attack.py configs/Test_enterprise_multi_as_structured.yml \
  --attackers pc_e pc_f \
  --target 10.40.10.10 \
  --port 9000 \
  --duration 30 \
  --workers 20 \
  --delay-ms 20
```
