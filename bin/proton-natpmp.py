#!/usr/bin/env python3
"""
Proton VPN NAT-PMP Port Forwarding Daemon for Syncthing
Author: yb007-sketch

Automates NAT-PMP port lease renewal for Proton VPN (P2P servers) and updates
Syncthing's external listen addresses dynamically.

Works out of the box with zero configuration by automatically discovering
Syncthing's local API key from config.xml.
"""

import socket
import struct
import time
import os
import ssl
import json
import urllib.request
import xml.etree.ElementTree as ET

# Network & Gateway Defaults
GATEWAY = os.environ.get("PROTON_GATEWAY", "10.2.0.1")
NATPMP_PORT = 5351
LEASE_LIFETIME = 60  # seconds
RENEW_INTERVAL = 45  # seconds
STATUS_FILE = os.path.expanduser("~/.local/state/syncthing/proton_port.txt")
SYNCTHING_URL = os.environ.get("SYNCTHING_URL", "https://127.0.0.1:8384")
SSL_CTX = ssl._create_unverified_context()

def get_syncthing_api_key():
    """Auto-detect API key from environment or Syncthing's config.xml."""
    if os.environ.get("SYNCTHING_API_KEY"):
        return os.environ.get("SYNCTHING_API_KEY")
    candidates = [
        os.path.expanduser("~/.local/state/syncthing/config.xml"),
        os.path.expanduser("~/.config/syncthing/config.xml"),
    ]
    for p in candidates:
        if os.path.exists(p):
            try:
                tree = ET.parse(p)
                key = tree.findtext("./gui/apikey")
                if key:
                    return key
            except Exception:
                pass
    return None

def request_mapping(proto_opcode, internal_port, lifetime=LEASE_LIFETIME):
    """Send NAT-PMP mapping request to the Proton VPN WireGuard/OpenVPN gateway."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0)
    try:
        req = struct.pack(">BBHHHI", 0, proto_opcode, 0, internal_port, 0, lifetime)
        sock.sendto(req, (GATEWAY, NATPMP_PORT))
        resp, _ = sock.recvfrom(1024)
        if len(resp) >= 16:
            v, op, res, epoch, int_p, ext_p, lt = struct.unpack(">BBHI HHI", resp[:16])
            if res == 0:
                return ext_p, lt
        return None, None
    except Exception:
        return None, None
    finally:
        sock.close()

def get_public_ip():
    """Retrieve public IP from Proton VPN gateway via NAT-PMP op 0."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(5.0)
    try:
        req = struct.pack(">BB", 0, 0)
        sock.sendto(req, (GATEWAY, NATPMP_PORT))
        resp, _ = sock.recvfrom(1024)
        if len(resp) >= 12:
            v, op, res, epoch, ip_int = struct.unpack(">BBHI I", resp[:12])
            if res == 0:
                return socket.inet_ntoa(struct.pack(">I", ip_int))
        return None
    except Exception:
        return None
    finally:
        sock.close()

def update_syncthing_listen_address(public_ip, ext_port, api_key):
    """Announce forwarded port to Syncthing if not already configured."""
    if not api_key:
        return
    wan_target = f"tcp://{public_ip}:{ext_port}"
    try:
        headers = {"X-API-Key": api_key, "Content-Type": "application/json"}
        req = urllib.request.Request(f"{SYNCTHING_URL}/rest/config", headers=headers)
        with urllib.request.urlopen(req, context=SSL_CTX, timeout=5) as resp:
            config = json.loads(resp.read().decode())
        
        current_addrs = config.get("options", {}).get("listenAddresses", [])
        if "default" not in current_addrs:
            current_addrs.append("default")
            config["options"]["listenAddresses"] = current_addrs
            put_req = urllib.request.Request(
                f"{SYNCTHING_URL}/rest/config",
                data=json.dumps(config).encode(),
                headers=headers,
                method="PUT"
            )
            with urllib.request.urlopen(put_req, context=SSL_CTX, timeout=5) as r:
                pass
    except Exception:
        pass

def write_status_file(public_ip, port):
    """Persist port info for dashboard and diagnostic scripts."""
    try:
        os.makedirs(os.path.dirname(STATUS_FILE), exist_ok=True)
        with open(STATUS_FILE, "w") as f:
            f.write(f"IP={public_ip}\nPORT={port}\nUPDATED={int(time.time())}\n")
    except Exception:
        pass

def main():
    print("Starting Proton VPN NAT-PMP Port Forwarding Daemon...")
    api_key = get_syncthing_api_key()
    if not api_key:
        print("Warning: Syncthing API key could not be detected. Will continue NAT-PMP mapping.")
    else:
        print("Detected Syncthing configuration.")

    while True:
        pub_ip = get_public_ip()
        tcp_p, _ = request_mapping(proto_opcode=1, internal_port=22000)
        udp_p, _ = request_mapping(proto_opcode=2, internal_port=22000)

        active_port = tcp_p or udp_p
        if active_port and pub_ip:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Active Port: {active_port} on Exit IP {pub_ip}")
            write_status_file(pub_ip, active_port)
            if api_key:
                update_syncthing_listen_address(pub_ip, active_port, api_key)
        else:
            print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] Waiting for Proton VPN WireGuard tunnel...")

        time.sleep(RENEW_INTERVAL)

if __name__ == "__main__":
    main()
