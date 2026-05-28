# client-router-server

Laboratorio Kathara generato automaticamente.

## Nodi

### client

- Tipo: `host`
- eth0: rete `lan1`, IP `10.0.1.10/24`
- Gateway: `10.0.1.1`

### router

- Tipo: `router`
- eth0: rete `lan1`, IP `10.0.1.1/24`
- eth1: rete `dmz`, IP `10.0.2.1/24`

### server

- Tipo: `host`
- eth0: rete `dmz`, IP `10.0.2.10/24`
- Gateway: `10.0.2.1`

## Avvio

Avvio standard:

```bash
kathara lstart
```

Avvio con script generato, utile anche per Wireshark:

```bash
./start_lab.sh
```

## Pulizia

Pulizia standard:

```bash
kathara lclean
```

Oppure:

```bash
./stop_lab.sh
```
