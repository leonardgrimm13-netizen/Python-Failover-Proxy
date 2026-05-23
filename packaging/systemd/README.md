# systemd packaging (mc-failover)

## 1) Create service user

```bash
sudo useradd --system --home /var/lib/mc-failover --shell /usr/sbin/nologin mcfailover
```

## 2) Create directories

```bash
sudo mkdir -p /opt/mc-failover /etc/mc-failover /var/lib/mc-failover /run/mc-failover
```

## 3) Copy files

```bash
sudo cp mc_failover_proxy.py /opt/mc-failover/
sudo cp requirements.txt /opt/mc-failover/
sudo cp config.example.toml /etc/mc-failover/config.toml
```

## 4) Set ownership and permissions

```bash
sudo chown -R root:root /opt/mc-failover /etc/mc-failover
sudo chown -R mcfailover:mcfailover /var/lib/mc-failover /run/mc-failover
sudo chmod 644 /etc/mc-failover/config.toml
```

## 5) Install dependencies

```bash
cd /opt/mc-failover
sudo python3 -m pip install -r requirements.txt
```

Optional: use a virtual environment

```bash
python3 -m venv /opt/mc-failover/.venv
/opt/mc-failover/.venv/bin/python -m pip install -r /opt/mc-failover/requirements.txt
```

If using the venv, update `ExecStart` in `packaging/systemd/mc-failover.service` to point to `/opt/mc-failover/.venv/bin/python`.

## 6) Install and enable service

```bash
sudo cp packaging/systemd/mc-failover.service /etc/systemd/system/mc-failover.service
sudo systemctl daemon-reload
sudo systemctl enable --now mc-failover
```

## 7) Check logs

```bash
systemctl status mc-failover
journalctl -u mc-failover -f
```
