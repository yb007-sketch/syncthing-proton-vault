# Android Setup Guide (Syncthing-Fork + Full Root Storage)

This guide walks you through pairing an Android phone with your laptop's Syncthing cluster, granting full storage access to `/storage/emulated/0`, and enabling continuous background sync over both Home Wi-Fi (LAN) and Mobile Cellular (4G/5G).

---

## 1. Prerequisites

1. **Syncthing-Fork**: Install **Syncthing-Fork** (by Catfriend1) from [F-Droid](https://f-droid.org/packages/com.github.catfriend1.syncthingfork/) or [GitHub Releases](https://github.com/Catfriend1/syncthing-android/releases).
   > **Why Syncthing-Fork?** Google's official Play Store build restricts storage access due to Play Store policies. Syncthing-Fork supports full `MANAGE_EXTERNAL_STORAGE` (All Files Access) and Scoped Storage bypass.

---

## 2. Permissions & Run Conditions

### Step 2.1: Grant "All Files Access"
1. On your phone, go to **Settings > Apps > Special App Access > All Files Access**.
2. Locate **Syncthing-Fork** and toggle **Allow management of all files** to **ON**.

### Step 2.2: Disable Battery Optimizations
1. In phone Settings, go to **Apps > Syncthing-Fork > Battery**.
2. Set to **Unrestricted** (or "Don't optimize").

### Step 2.3: Configure Syncthing-Fork Run Conditions
1. Open **Syncthing-Fork**.
2. Tap the top-right 3-dots menu > **Settings > Syncthing Options > Run conditions**:
   - Check **Run on Wi-Fi** AND **Run on mobile data**.
   - Under **Run when device is powered by**, select **AC and battery power**.
   - Uncheck **Pause on battery saver** (optional, recommended for continuous backup).

---

## 3. Pairing Phone with Laptop

1. **Find Phone Device ID**:
   - In Syncthing-Fork, tap 3-dots menu > **Show device ID** (or tap the QR code icon).
2. **Add Device in Dashboard**:
   - On your laptop, open the dashboard at `http://localhost:8386`.
   - Go to the **⚙️ Settings & Control Center** tab.
   - Click **Add New Device**.
   - Paste the Phone's Device ID and enter a friendly name (e.g. `My Phone`).
   - Leave addresses as `dynamic`.
   - Click **Pair Device**.
3. **Accept Connection on Phone**:
   - Within 10 seconds, a notification will appear on your phone: *"Device wants to connect"*.
   - Tap **Accept**.

---

## 4. Selecting Full Root Storage (`/storage/emulated/0`)

Android 11+ SAF (Storage Access Framework) often blocks selecting the root of internal storage with the message *"Can't use this folder"*. Use Syncthing-Fork's built-in bypass:

1. When adding or accepting the shared folder in Syncthing-Fork, tap **Directory**.
2. If Android's system file picker blocks `/storage/emulated/0`:
   - Tap the **3-dots menu** inside Syncthing-Fork's directory chooser.
   - Select **Advanced Directory Selection** (or type `/storage/emulated/0` directly).
   - Confirm selection.
3. Set **Folder Type** to **Send & Receive** (bidirectional sync).
4. Tap the check mark icon to save.

---

## 5. Proton VPN Split Tunneling (Important!)

If you run Proton VPN on both your phone and laptop:
- **Issue**: Both devices may be assigned the same internal VPN IP (`10.2.0.2`), causing kernel packet drops (Martian packets).
- **Recommended Setup**:
  - In the Proton VPN app on your **Phone**: Open **Settings > Advanced > Split Tunneling**.
  - Toggle Split Tunneling to **ON** and choose **Exclude apps**.
  - Check **Syncthing-Fork** so Syncthing traffic connects directly over cellular/Wi-Fi to your laptop's forwarded port without VPN encapsulation.
