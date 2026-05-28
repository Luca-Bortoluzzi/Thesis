# multi-lans

Topologia con più LAN collegate tramite router in maglia estesa con OSPF.

## Nodi

### pc_a

- Tipo: `host`
- eth0: rete `lan_a`, IP `10.10.1.10/24`
- Gateway: `10.10.1.1`

### pc_b

- Tipo: `host`
- eth0: rete `lan_b`, IP `10.10.2.10/24`
- Gateway: `10.10.2.1`

### pc_c

- Tipo: `host`
- eth0: rete `lan_c`, IP `10.10.3.10/24`
- Gateway: `10.10.3.1`

### pc_d

- Tipo: `host`
- eth0: rete `lan_d`, IP `10.10.4.10/24`
- Gateway: `10.10.4.1`

### r1

- Tipo: `router`
- eth0: rete `lan_a`, IP `10.10.1.1/24`
- eth1: rete `r1_r2`, IP `10.0.12.1/30`
- eth2: rete `r1_r3`, IP `10.0.13.1/30`
- FRR: `ospf`

### r2

- Tipo: `router`
- eth0: rete `lan_b`, IP `10.10.2.1/24`
- eth1: rete `r1_r2`, IP `10.0.12.2/30`
- eth2: rete `r2_r3`, IP `10.0.23.1/30`
- eth3: rete `r2_r4`, IP `10.0.24.1/30`
- FRR: `ospf`

### r3

- Tipo: `router`
- eth0: rete `lan_c`, IP `10.10.3.1/24`
- eth1: rete `r1_r3`, IP `10.0.13.2/30`
- eth2: rete `r2_r3`, IP `10.0.23.2/30`
- eth3: rete `r3_r4`, IP `10.0.34.1/30`
- FRR: `ospf`

### r4

- Tipo: `router`
- eth0: rete `lan_d`, IP `10.10.4.1/24`
- eth1: rete `r2_r4`, IP `10.0.24.2/30`
- eth2: rete `r3_r4`, IP `10.0.34.2/30`
- FRR: `ospf`

## Avvio

```bash
kathara lstart
```

## Pulizia

```bash
kathara lclean
```
