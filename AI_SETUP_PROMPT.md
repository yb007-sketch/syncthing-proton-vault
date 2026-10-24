# AI Setup Prompt for Autonomous Coding Agents
### Supported Agents: Claude Code, Codex, Antigravity AI, Cursor, Windsurf

If you ever move to a new laptop or want an AI assistant to set up this complete Syncthing + Proton VPN + 60-Day Retention Hub from scratch, copy the prompt below and paste it into your AI assistant.

---

### Copy & Paste the Prompt Below:

```text
You are an expert systems engineer and pair programmer. I want you to set up my complete Syncthing + Android + Proton VPN automated sync suite and interactive dashboard on this Linux system using the open-source repository: https://github.com/yb007-sketch/syncthing-proton-vault.git

Please execute the following steps autonomously:

1. REPOSITORY CLONING & INSPECTION:
   - Clone https://github.com/yb007-sketch/syncthing-proton-vault.git into ~/syncthing-proton-vault (or pull latest if already present).
   - Inspect the host environment: verify Python 3, Syncthing, systemd user session, and network interfaces.

2. RUN AUTOMATED INSTALLER:
   - Execute `bash ~/syncthing-proton-vault/install.sh`.
   - Ensure the scripts (~/.local/bin/proton-natpmp.py and ~/.local/bin/syncthing-dashboard.py) are executable and installed.
   - Ensure the user systemd services (proton-natpmp.service and syncthing-dashboard.service) are enabled and running.

3. CONFIGURE SYNCTHING 60-DAY RETENTION VAULT:
   - Inspect the local Syncthing configuration at ~/.local/state/syncthing/config.xml (or ~/.config/syncthing/config.xml).
   - Read the local API key automatically from config.xml (do not prompt me for secrets).
   - Configure the target sync folder (default ~/AndroidSync/Storage or existing secondary drive mount) with 60-Day Trashcan Versioning:
     - Type: "trashcan"
     - Params: cleanoutDays = "60"
     - CleanupIntervalS: 3600
     - fsPath: ~/AndroidSync/Retention_60Days (or dedicated retention directory)
   - Install the optimized Android .stignore template ignoring (?d)Android, (?d)LOST.DIR, and cache/thumbnail files.

4. AUTOMATED PROTON VPN PORT FORWARDING:
   - Verify proton-natpmp.service is actively querying the Proton VPN WireGuard gateway (10.2.0.1:5351).
   - Ensure it writes the active public port to ~/.local/state/syncthing/proton_port.txt.
   - Verify that Syncthing's listenAddresses includes "default" so remote devices can resolve the dynamic WAN port.

5. VERIFICATION & DASHBOARD URL:
   - Check that the interactive dashboard is responding on http://127.0.0.1:8386 and accessible across the local network at http://<LOCAL_LAN_IP>:8386.
   - Verify the dashboard displays:
     * Active storage folder size in GB
     * Individual files in MB
     * 60-day retention holding vault with Days Left countdown
     * Working 1-click "Open on Laptop" (Nautilus/xdg-open integration)
     * Full Settings & Control Center with in-dashboard .stignore code editor
   - Report the status of all services and provide instructions to pair the Android phone via Syncthing-Fork.
```
