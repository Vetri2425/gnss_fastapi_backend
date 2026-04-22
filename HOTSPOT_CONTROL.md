# DYX_BASE Hotspot Internet Sharing Control

## Quick Commands

### Check Status
```bash
sudo /usr/local/bin/dyx-hotspot-share.sh status
```

### Enable Internet Sharing
```bash
sudo /usr/local/bin/dyx-hotspot-share.sh enable
```

### Disable Internet Sharing
```bash
sudo /usr/local/bin/dyx-hotspot-share.sh disable
```

---

## Configuration File

**Location:** `/etc/dyx-hotspot.conf`

Edit this file to customize hotspot settings, then restart the service:

```bash
sudo nano /etc/dyx-hotspot.conf
sudo systemctl restart dyx-hotspot-share.service
```

### Configuration Options

| Option | Default | Description |
|--------|---------|-------------|
| `INTERNET_SHARING_ENABLED` | 1 | Enable (1) or disable (0) internet sharing |
| `GATEWAY_INTERFACE` | eth0 | Interface connected to internet |
| `HOTSPOT_INTERFACE` | wlan0 | WiFi interface for hotspot |
| `UPSTREAM_DNS` | 8.8.8.8,8.8.4.4 | Upstream DNS servers (comma-separated) |
| `DHCP_START` | 192.168.4.2 | DHCP range start IP |
| `DHCP_END` | 192.168.4.50 | DHCP range end IP |
| `HOTSPOT_IP` | 192.168.4.1 | Hotspot gateway IP |
| `HOTSPOT_SUBNET` | 255.255.255.0 | Subnet mask |

---

## How It Works

### Enable Mode
When internet sharing is **ENABLED** (1):
- ✅ IP forwarding enabled (`/proc/sys/net/ipv4/ip_forward = 1`)
- ✅ NAT rule added (`iptables MASQUERADE on eth0`)
- ✅ dnsmasq configured with upstream DNS
- ✅ Clients can access internet + API

### Disable Mode
When internet sharing is **DISABLED** (0):
- ❌ IP forwarding disabled
- ❌ NAT rule removed
- ❌ DNS forwarding disabled
- ✅ Clients can still access local API (`http://192.168.4.1:8000`)
- ✅ No external internet access

---

## Service Management

### View Service Status
```bash
sudo systemctl status dyx-hotspot-share.service
```

### View Service Logs
```bash
sudo journalctl -u dyx-hotspot-share.service -n 20
```

### Restart Service
```bash
sudo systemctl restart dyx-hotspot-share.service
```

### Enable at Boot
```bash
sudo systemctl enable dyx-hotspot-share.service
```

### Disable at Boot
```bash
sudo systemctl disable dyx-hotspot-share.service
```

---

## Use Cases

### Scenario 1: Clients Need Internet Access
```bash
# Edit config file
sudo nano /etc/dyx-hotspot.conf
# Set: INTERNET_SHARING_ENABLED=1

# Restart service
sudo systemctl restart dyx-hotspot-share.service
```

### Scenario 2: Only API Access (No Internet)
```bash
# Edit config file
sudo nano /etc/dyx-hotspot.conf
# Set: INTERNET_SHARING_ENABLED=0

# Restart service
sudo systemctl restart dyx-hotspot-share.service
```

### Scenario 3: Change DNS Servers
```bash
# Edit config file
sudo nano /etc/dyx-hotspot.conf
# Change: UPSTREAM_DNS=1.1.1.1,1.0.0.1

# Restart service
sudo systemctl restart dyx-hotspot-share.service
```

### Scenario 4: Expand DHCP Range
```bash
# Edit config file
sudo nano /etc/dyx-hotspot.conf
# Change: DHCP_START=192.168.4.2
# Change: DHCP_END=192.168.4.200

# Restart service
sudo systemctl restart dyx-hotspot-share.service
```

---

## Verification

### Check Internet Sharing Status
```bash
# From DYX_BASE device
cat /proc/sys/net/ipv4/ip_forward
# Expected: 1 (enabled) or 0 (disabled)

# Check NAT rules
sudo iptables -t nat -L POSTROUTING -v -n
# Expected: MASQUERADE rule on eth0 (if enabled)

# Check DNS config
cat /etc/dnsmasq.d/dyx-ap.conf
# Expected: server= lines (if enabled)
```

### Test from Client
```bash
# If ENABLED - should work:
ping 8.8.8.8
nslookup google.com 192.168.4.1
curl http://google.com

# If DISABLED - should work:
curl http://192.168.4.1:8000/api/v1/health

# Always works:
curl http://192.168.4.1:8000/api/v1/status
```

---

## Troubleshooting

### Service won't start
```bash
sudo systemctl status dyx-hotspot-share.service
sudo journalctl -u dyx-hotspot-share.service -n 50
```

### DNS not working
```bash
# Check if dnsmasq is running
sudo systemctl status dnsmasq

# Check if config was updated
cat /etc/dnsmasq.d/dyx-ap.conf

# Verify upstream DNS
grep "^server=" /etc/dnsmasq.d/dyx-ap.conf
```

### No internet despite ENABLED
```bash
# Check IP forwarding
cat /proc/sys/net/ipv4/ip_forward
# Should be: 1

# Check NAT rules
sudo iptables -t nat -L POSTROUTING -v -n
# Should show: MASQUERADE on eth0

# Check routing
ip route
# Should have: default via 192.168.1.1
```

### Clients getting no IP
```bash
# Check DHCP is running
sudo systemctl status dnsmasq

# Check DHCP config
cat /etc/dnsmasq.d/dyx-ap.conf
# Should have: dhcp-range=

# Check lease file
cat /var/lib/misc/dnsmasq.leases
```

---

## Files Reference

| File | Purpose |
|------|---------|
| `/etc/dyx-hotspot.conf` | Configuration (user-editable) |
| `/usr/local/bin/dyx-hotspot-share.sh` | Control script |
| `/etc/systemd/system/dyx-hotspot-share.service` | Systemd service |
| `/etc/dnsmasq.d/dyx-ap.conf` | dnsmasq config (auto-generated) |
| `/etc/iptables/rules.v4` | iptables rules persistence |

---

## Related Services

### hostapd (WiFi Access Point)
```bash
sudo systemctl status hostapd
sudo systemctl restart hostapd
```

### dnsmasq (DNS + DHCP)
```bash
sudo systemctl status dnsmasq
sudo systemctl restart dnsmasq
```

### GNSS Backend (API Server)
```bash
sudo systemctl status gnss-backend
sudo systemctl restart gnss-backend
```

### OLED Display
```bash
sudo systemctl status oled_animation
sudo systemctl restart oled_animation
```

---

## Persistent Changes

All changes are **persistent across reboots** because:
- Config file is in `/etc/` (survives reboot)
- Service is enabled (`systemctl enable`)
- iptables rules are saved to `/etc/iptables/rules.v4`

To revert to defaults:
```bash
# Disable service
sudo systemctl disable dyx-hotspot-share.service

# Restore default config
sudo cp /etc/dyx-hotspot.conf.bak /etc/dyx-hotspot.conf
# Or edit manually
```
