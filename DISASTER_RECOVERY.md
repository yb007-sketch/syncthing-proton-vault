# Disaster Recovery Runbook: Losing Phone or Laptop

This runbook provides step-by-step recovery procedures for two scenarios:
1. **Scenario A**: Your Android Phone is lost, stolen, or damaged.
2. **Scenario B**: Your Laptop OS fails, disk is replaced, or you move to a new laptop.

---

## Scenario A: Phone is Lost, Stolen, or Replaced

All your photos, camera images, documents, and downloads are already safely stored on your laptop under your sync directory (e.g. `/media/spicykiller/New Volume/Syncthing/Nothing Phone 3A/` or `~/AndroidSync/Storage/`).

### Step A.1: Unpair the Lost Phone (Security Precaution)
1. On your laptop, open the dashboard at [http://localhost:8386](http://localhost:8386).
2. Go to **⚙️ Settings & Control Center > Connected Devices**.
3. Locate the lost phone in the table and click **Remove / Unpair**. This revokes the lost phone's cryptographic certificate and prevents it from connecting or syncing.

### Step A.2: Set Up the New Replacement Phone
1. Install **Syncthing-Fork** (by Catfriend1) on your new phone.
2. Grant **All Files Access** (`MANAGE_EXTERNAL_STORAGE`) and set Battery to **Unrestricted** (see [docs/ANDROID_SETUP_GUIDE.md](docs/ANDROID_SETUP_GUIDE.md)).
3. In Syncthing-Fork, tap 3-dots menu > **Show device ID**.
4. In your laptop dashboard (`http://localhost:8386`):
   - Click **Add New Device**.
   - Paste the new phone's Device ID and name it `New Phone`.
   - Keep address as `dynamic` and click **Pair Device**.
5. On the new phone, tap **Accept** on the pairing notification.
6. Share your storage folder (`Nothing Phone Storage`) with the new phone:
   - On the new phone, accept the shared folder notification.
   - Choose `/storage/emulated/0` using **Advanced Directory Selection**.
   - Tap **Save** (check mark).
7. Syncthing will immediately begin downloading all your files, photos, and folders from your laptop onto the new phone at full Wi-Fi speed!

---

## Scenario B: Laptop Fails, Disk Replaced, or Moving to New Laptop

Your Android phone holds the complete master copy of your data (`/storage/emulated/0`).

### Step B.1: Prepare the New Laptop
1. Boot into your new Linux system (Ubuntu, Zorin, Fedora, Debian, Mint, Arch).
2. Ensure Python 3 is installed.
3. Install Syncthing:
   ```bash
   sudo apt update && sudo apt install -y syncthing
   # Start syncthing user service
   systemctl --user enable --now syncthing
   ```

### Step B.2: Fast Turn-Key Setup via Autonomous AI or Installer
Choose between two methods:

- **Method 1: 1-Click Automated Script**:
  ```bash
  git clone https://github.com/yb007-sketch/syncthing-proton-vault.git ~/syncthing-proton-vault
  cd ~/syncthing-proton-vault
  bash install.sh
  ```
- **Method 2: AI Assistant Prompt**:
  Open Claude Code, Codex, or Antigravity and paste the prompt in [AI_SETUP_PROMPT.md](AI_SETUP_PROMPT.md). The AI will autonomously configure everything.

### Step B.3: Pair with Your Existing Phone
1. Open the dashboard at [http://localhost:8386](http://localhost:8386).
2. On your existing phone, open Syncthing-Fork > **Show device ID**.
3. In the dashboard, click **Add New Device**, paste your phone's Device ID, and save.
4. On your phone, accept the incoming connection from the new laptop.
5. In Syncthing-Fork on your phone, edit the existing sync folder:
   - Under the **Sharing** tab, check the box next to your new laptop.
   - Tap **Save**.
6. The phone will immediately begin streaming all 9,700+ files (~6+ GB) directly to your new laptop!

### Step B.4: Reconnect Proton VPN Port Forwarding
1. Open your Proton VPN app on the new laptop and log into your Plus/Unlimited account.
2. In Settings > Connection, toggle **Port Forwarding** to **ON**.
3. Connect to any P2P server (e.g. London UK).
4. The background daemon (`proton-natpmp.service`) will automatically pick up the new WireGuard gateway, lease the port, and announce it to Syncthing for remote 4G/5G sync!
