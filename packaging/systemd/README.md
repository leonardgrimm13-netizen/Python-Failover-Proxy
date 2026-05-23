# systemd deployment for Minecraft Python Failover Proxy

This guide installs and runs the proxy as a dedicated unprivileged Linux service user.

## 1) Create service user

```bash
sudo useradd --system --home /var/lib/mc-failover --shell /usr/sbin/nologin mcfailover
```

## 2) Create directories

```bash
sudo mkdir -p /opt/mc-failover /etc/mc-failover /var/lib/mc-failover /run/mc-failover
```

## 3) Copy project files

Run from the repository root:

```bash
sudo cp mc_failover_proxy.py /opt/mc-failover/
sudo cp requirements.txt /opt/mc-failover/
sudo cp config.example.toml /etc/mc-failover/config.toml
```

Then edit `/etc/mc-failover/config.toml` for your MAIN/FALLBACK setup.

## 4) Set permissions

```bash
sudo chown -R root:root /opt/mc-failover /etc/mc-failover
sudo chown -R mcfailover:mcfailover /var/lib/mc-failover /run/mc-failover
sudo chmod 644 /etc/mc-failover/config.toml
```

## 5) Install dependencies (recommended: virtual environment)

Using a venv avoids distro-level `pip` restrictions (for example PEP 668 / externally-managed environments on Ubuntu/Debian).

```bash
python3 -m venv /opt/mc-failover/.venv
/opt/mc-failover/.venv/bin/python -m pip install -r /opt/mc-failover/requirements.txt
```

Update `ExecStart` in `packaging/systemd/mc-failover.service` to use the venv interpreter:

```ini
ExecStart=/opt/mc-failover/.venv/bin/python /opt/mc-failover/mc_failover_proxy.py --config /etc/mc-failover/config.toml
```

Optional fallback (not preferred): system-wide install

```bash
cd /opt/mc-failover
sudo python3 -m pip install -r requirements.txt
```

## 6) Install and start service

```bash
sudo cp packaging/systemd/mc-failover.service /etc/systemd/system/mc-failover.service
sudo systemctl daemon-reload
sudo systemctl enable --now mc-failover
```

## 7) Check service logs

```bash
systemctl status mc-failover
journalctl -u mc-failover -f
```
