# Multi-Device Pairing Guide (Adding 2nd Phone, Tablet, or Laptop)

Syncthing supports pairing an unlimited number of Android phones, tablets, and computers into your personal backup mesh.

---

## 1. Setup Choices: Shared Folder vs. Dedicated Folder

When adding a second device (e.g. Phone 2), choose between two architectures:

### Option A: Dedicated Folder per Device (Recommended)
Each phone gets its own dedicated backup directory on your laptop:
- Laptop Directory: `/media/your-disk/Syncthing/Phone 2/`
- Retention Holding: `/media/your-disk/Syncthing/Retention_60Days/Phone2/`
- **Benefit**: Keeps photos and files from different phones completely separated.

### Option B: Shared Folder Across Devices
All devices sync into the same shared folder:
- **Benefit**: Photos taken on Phone 1 appear on Phone 2 and your laptop simultaneously.

---

## 2. Step-by-Step Pairing via the Dashboard

1. **Get Device ID from New Phone**:
   - Install **Syncthing-Fork** on the new device.
   - Open 3-dots menu > **Show device ID**.
2. **Pair in Dashboard**:
   - On your laptop, open `http://localhost:8386`.
   - Go to **⚙️ Settings & Control Center > Connected Devices**.
   - Click **Add New Device**.
   - Paste the Device ID, enter a friendly name (e.g. `OnePlus 12`), and set addresses to `dynamic`.
   - Click **Pair Device**.
3. **Accept on the New Phone**:
   - Tap **Accept** on the incoming pairing notification on Phone 2.
4. **Share Folder**:
   - In the Dashboard under **Folders Configuration**, click **Add New Folder** (or edit an existing folder) and check the box next to Phone 2.
   - On Phone 2, accept the folder invitation, choose `/storage/emulated/0` (via Advanced Directory Selection), and tap Save.
5. **Automatic Dashboard Discovery**:
   - The dashboard automatically detects the new device and folder, updating the top **Folder Selector Dropdown** and stats immediately.
