# Syncthing Interactive Dashboard & Control Center Guide

The suite includes an interactive, zero-dependency web dashboard and control center built in Python.

---

## 1. Accessing the Dashboard

- **On Laptop**: [http://localhost:8386](http://localhost:8386)
- **On Phone or LAN**: `http://<LAPTOP_LOCAL_IP>:8386` (e.g. `http://192.168.50.55:8386`)

---

## 2. Key Modules & Features

### 📁 In-Dashboard File Explorer
- **Folder Navigation**: Drill down through directories (`DCIM/Camera`, `Pictures`, `Download`, etc.) with breadcrumbs.
- **Size Displays**: Folders displayed in **GB** (`2.06 GB`), individual files in **MB** (`6.83 MB`).
- **Media Previewer**: In-browser preview for images, video streaming, audio player, and PDF/text viewers.
- **📂 Open on Laptop**: Clicking **"Reveal on Laptop"** opens the desktop file manager (Nautilus) and highlights the specific file on your laptop screen.

### 🛡️ 60-Day Retention Vault
- **Holding Period**: Files deleted on your phone are preserved in your retention folder for **60 days**.
- **No Resync Guarantee**: Retained files are never synced back to the phone automatically.
- **Countdown Badges**: Visual indicator showing remaining days before permanent purging:
  - 🟢 **> 30 Days Left**: Safe
  - 🟡 **10–30 Days Left**: Warning
  - 🔴 **< 10 Days Left**: Expiring Soon
- **1-Click Restore**: Recovers the deleted file back to active storage with one click.

### ⚙️ Settings & Control Center
- **Folder Configuration**: Add new folders, pause/resume sync, trigger rescans, or open folders in file manager.
- **.stignore Editor**: View and edit ignore rules directly in an in-dashboard code editor with 1-click Save.
- **Device Management**: Pair new phones, rename devices, tune peer addresses (`dynamic`), or pause sync.
- **System Controls**: Restart Syncthing engine, force full rescan, and refresh the Proton VPN port lease.

---

## 3. REST API Reference

| Endpoint | Method | Description |
|---|---|---|
| `/api/overview` | `GET` | System KPI metrics, device connection status, storage stats |
| `/api/folders` | `GET` | Folder breakdown with sizes in GB and item counts |
| `/api/browse` | `GET` | Directory contents with files in MB and folders in GB |
| `/api/file-content` | `GET` | Stream file content for in-browser preview or download |
| `/api/open-local` | `POST` | Opens folder or highlights file in Linux file manager |
| `/api/daily-activity`| `GET` | 14-day history of transfer volume (MB) and file counts |
| `/api/sync-log` | `GET` | Live stream of added, updated, and deleted files |
| `/api/retention-files`| `GET` | Files in 60-day holding with countdown days |
| `/api/restore` | `POST` | Restores a retained file to active storage |
| `/api/config/full` | `GET` | Full Syncthing configuration and device health |
| `/api/config/folder/add` | `POST` | Create a new sync folder with versioning |
| `/api/config/folder/stignore` | `GET/POST` | Read or save `.stignore` rules |
| `/api/config/device/add` | `POST` | Pair a new remote device |
| `/api/system/restart` | `POST` | Dispatches Syncthing restart |
