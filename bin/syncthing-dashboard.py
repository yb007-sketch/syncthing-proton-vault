#!/usr/bin/env python3
"""
Syncthing Advanced Interactive Hub & 60-Day Retention Vault
Features:
- Dynamic discovery of ALL folders & devices (auto-picks up new folders and devices)
- Interactive In-Dashboard File Explorer (drill-down, see actual files, preview/download)
- 1-Click "Open in Laptop File Manager" (Nautilus/xdg-open integration)
- Folder size in GB & individual file size in MB formatting
- Full Syncthing Settings Control: Folders, Devices, .stignore editor, Rescan, Pause/Resume, Restart
- 60-Day Retention Vault with Days-Left countdown & 1-Click Restore
- Real-time Charts for Daily Sync Activity (MB) & Folder Breakdown (GB)
- Accessible on http://0.0.0.0:8386 (LAN: http://192.168.50.55:8386)
"""

import os
import sys
import json
import time
import ssl
import shutil
import sqlite3
import threading
import subprocess
import mimetypes
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from datetime import datetime, date, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler
from socketserver import ThreadingMixIn

# Configurations
SYNCTHING_API_URL = os.environ.get("SYNCTHING_URL", "https://127.0.0.1:8384")
PORT = int(os.environ.get("PORT", 8386))
DEFAULT_CLEANOUT_DAYS = 60
DB_PATH = os.path.expanduser("~/.local/state/syncthing/dashboard.db")
VPN_PORT_FILE = os.path.expanduser("~/.local/state/syncthing/proton_port.txt")

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

# Thread Locks & Caches
db_lock = threading.Lock()
folder_cache_lock = threading.Lock()
folder_cache = {}  # folder_id -> { last_scanned, folders, total_size, total_files }

ssl_ctx = ssl._create_unverified_context()

def get_db_connection():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn

# Formatting Helpers
def format_folder_gb(size_bytes):
    if size_bytes is None or size_bytes == 0:
        return "0.00 GB"
    gb = size_bytes / (1024.0 * 1024.0 * 1024.0)
    return f"{gb:.2f} GB"

def format_file_mb(size_bytes):
    if size_bytes is None or size_bytes == 0:
        return "0.00 MB"
    mb = size_bytes / (1024.0 * 1024.0)
    if mb < 0.01:
        return "< 0.01 MB"
    return f"{mb:.2f} MB"

def format_bytes_auto(size):
    if size is None:
        return "0 B"
    for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
        if abs(size) < 1024.0:
            return f"{size:3.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"

# Syncthing API Client Helpers
def syncthing_req(endpoint, method='GET', data=None):
    url = f"{SYNCTHING_API_URL}{endpoint}"
    api_key = get_syncthing_api_key() or ""
    headers = {'X-API-Key': api_key}
    body = None
    if data is not None:
        headers['Content-Type'] = 'application/json'
        body = json.dumps(data).encode('utf-8')
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, context=ssl_ctx, timeout=8) as resp:
            content = resp.read().decode('utf-8')
            if content:
                try:
                    return json.loads(content)
                except Exception:
                    return content
            return True
    except Exception as e:
        return None

def get_all_folders_config():
    cfg = syncthing_req("/rest/config/folders")
    return cfg if isinstance(cfg, list) else []

def get_all_devices_config():
    cfg = syncthing_req("/rest/config/devices")
    return cfg if isinstance(cfg, list) else []

def open_local_path(target_path):
    if not target_path:
        return False, "No path provided"
    target_path = os.path.abspath(os.path.expanduser(target_path))
    if not os.path.exists(target_path):
        return False, f"Target path does not exist on disk: {target_path}"
    
    env = os.environ.copy()
    if 'DISPLAY' not in env:
        env['DISPLAY'] = ':1'
    if 'DBUS_SESSION_BUS_ADDRESS' not in env:
        env['DBUS_SESSION_BUS_ADDRESS'] = 'unix:path=/run/user/1000/bus'
        
    try:
        if os.path.isfile(target_path):
            subprocess.Popen(['nautilus', '--select', target_path], env=env)
        else:
            subprocess.Popen(['xdg-open', target_path], env=env)
        return True, f"Opened {os.path.basename(target_path)} on laptop file manager"
    except Exception as e:
        return False, str(e)

# Storage & Retention Scanners
def scan_single_folder(folder_id, folder_path):
    if not os.path.exists(folder_path):
        return
    results = []
    total_size = 0
    total_files = 0
    
    try:
        entries = sorted(os.scandir(folder_path), key=lambda e: e.name.lower())
        for entry in entries:
            if entry.name.startswith('.'):
                continue
            f_size = 0
            f_count = 0
            if entry.is_dir():
                for root, _, files in os.walk(entry.path):
                    f_count += len(files)
                    for f in files:
                        try:
                            f_size += os.path.getsize(os.path.join(root, f))
                        except Exception:
                            pass
            elif entry.is_file():
                f_count = 1
                try:
                    f_size = entry.stat().st_size
                except Exception:
                    pass
            
            total_size += f_size
            total_files += f_count
            results.append({
                "name": entry.name,
                "is_dir": entry.is_dir(),
                "path": os.path.relpath(entry.path, folder_path),
                "absolute_path": entry.path,
                "files": f_count,
                "size_bytes": f_size,
                "size_gb": format_folder_gb(f_size),
                "size_mb": format_file_mb(f_size)
            })
            
        results.sort(key=lambda x: x["size_bytes"], reverse=True)
        for r in results:
            r["percentage"] = round((r["size_bytes"] / total_size * 100), 1) if total_size > 0 else 0
            
        with folder_cache_lock:
            folder_cache[folder_id] = {
                "last_scanned": time.time(),
                "folders": results,
                "total_size": total_size,
                "total_files": total_files,
                "path": folder_path
            }
    except Exception as e:
        print(f"Error scanning folder {folder_id}: {e}")

def scan_all_retention_vaults():
    folders = get_all_folders_config()
    current_time = time.time()
    discovered = []
    
    for f in folders:
        fid = f.get('id')
        ver = f.get('versioning', {})
        ret_path = ver.get('fsPath') or os.path.join(f.get('path', ''), '.stversions')
        cleanout = int(ver.get('params', {}).get('cleanoutDays', DEFAULT_CLEANOUT_DAYS) or DEFAULT_CLEANOUT_DAYS)
        
        if not os.path.exists(ret_path):
            continue
            
        for root, _, files in os.walk(ret_path):
            for filename in files:
                full_path = os.path.join(root, filename)
                rel_path = os.path.relpath(full_path, ret_path)
                parts = rel_path.split(os.sep)
                root_folder = parts[0] if len(parts) > 1 else "Root"
                
                try:
                    stat = os.stat(full_path)
                    mtime = stat.st_mtime
                    size = stat.st_size
                    age_days = (current_time - mtime) / 86400.0
                    days_left = max(0, cleanout - int(age_days))
                    deleted_ts = datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S")
                    
                    discovered.append((
                        rel_path,
                        filename,
                        root_folder,
                        fid,
                        full_path,
                        size,
                        deleted_ts,
                        mtime,
                        days_left,
                        'in_retention'
                    ))
                except Exception:
                    pass
                    
    with db_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        for item in discovered:
            cur.execute("""
            INSERT INTO retention_files 
            (relative_path, file_name, root_folder, folder_id, absolute_path, file_size, deleted_timestamp, mtime_epoch, days_remaining, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relative_path) DO UPDATE SET
                days_remaining = excluded.days_remaining,
                file_size = excluded.file_size,
                status = CASE WHEN status = 'restored' THEN 'restored' ELSE 'in_retention' END
            """, item)
        conn.commit()
        conn.close()

# Background Event Ingestor
def event_collector_worker():
    print("Dynamic Syncthing Event Collector thread active.")
    last_id = 0
    with db_lock:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT MAX(syncthing_event_id) FROM events")
        row = cur.fetchone()
        if row and row[0]:
            last_id = row[0]
        conn.close()
        
    while True:
        try:
            url = f"{SYNCTHING_API_URL}/rest/events?since={last_id}&limit=500"
            api_key = get_syncthing_api_key() or ""
            req = urllib.request.Request(url, headers={'X-API-Key': api_key})
            with urllib.request.urlopen(req, context=ssl_ctx, timeout=25) as resp:
                events = json.loads(resp.read().decode('utf-8'))
                
            if events:
                folders_cfg = {f['id']: f for f in get_all_folders_config()}
                devices_cfg = {d['deviceID']: d for d in get_all_devices_config()}
                
                with db_lock:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    has_deletes = False
                    
                    for ev in events:
                        eid = ev.get('id', 0)
                        if eid > last_id:
                            last_id = eid
                            
                        if ev.get('type') == 'ItemFinished':
                            ts = ev.get('time', '')
                            data = ev.get('data', {})
                            action = data.get('action') # 'update' or 'delete'
                            item = data.get('item', '')
                            folder_id = data.get('folder', '')
                            ftype = data.get('type', 'file')
                            err = data.get('error')
                            
                            folder_info = folders_cfg.get(folder_id, {})
                            folder_base = folder_info.get('path', '')
                            
                            parts = item.split('/') if item else []
                            root = parts[0] if parts else 'Root'
                            
                            size = 0
                            if action == 'update':
                                full = os.path.join(folder_base, item)
                                if os.path.exists(full):
                                    try:
                                        size = os.path.getsize(full)
                                    except Exception:
                                        pass
                            elif action == 'delete':
                                has_deletes = True
                                
                            dev_name = "Android Device"
                            cur.execute("""
                            INSERT OR IGNORE INTO events 
                            (syncthing_event_id, timestamp, folder_id, device_id, device_name, item_path, root_folder, action, file_type, file_size, error)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """, (eid, ts, folder_id, 'REMOTE', dev_name, item, root, action, ftype, size, err))
                            
                            day = ts[:10] if ts else datetime.now().strftime("%Y-%m-%d")
                            cur.execute("INSERT OR IGNORE INTO daily_stats (date, bytes_synced, files_added, files_updated, files_deleted) VALUES (?, 0, 0, 0, 0)", (day,))
                            if action == 'delete':
                                cur.execute("UPDATE daily_stats SET files_deleted = files_deleted + 1 WHERE date = ?", (day,))
                            elif action == 'update':
                                cur.execute("UPDATE daily_stats SET files_added = files_added + 1, bytes_synced = bytes_synced + ? WHERE date = ?", (size, day))
                                
                    conn.commit()
                    conn.close()
                    
                    if has_deletes:
                        scan_all_retention_vaults()
                        
            time.sleep(1)
        except Exception:
            time.sleep(3)

def periodic_scanner_worker():
    print("Storage & Retention scanner thread active.")
    while True:
        try:
            folders = get_all_folders_config()
            for f in folders:
                fid = f.get('id')
                fpath = f.get('path')
                with folder_cache_lock:
                    cached = folder_cache.get(fid)
                if not cached or (time.time() - cached.get("last_scanned", 0) > 180):
                    scan_single_folder(fid, fpath)
            scan_all_retention_vaults()
            time.sleep(60)
        except Exception:
            time.sleep(30)

# HTTP Server & Request Handler
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True

class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data, indent=2).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, PUT, DELETE, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()

    def do_HEAD(self):
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()

    def parse_body_json(self):
        length = int(self.headers.get('Content-Length', 0))
        if length > 0:
            raw = self.rfile.read(length)
            return json.loads(raw.decode('utf-8'))
        return {}

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        query = urllib.parse.parse_qs(url.query)

        if path == "/" or path == "/index.html":
            self.handle_dashboard_ui()
        elif path == "/api/overview":
            self.handle_overview(query)
        elif path == "/api/folders":
            self.handle_folders(query)
        elif path == "/api/browse":
            self.handle_browse(query)
        elif path == "/api/file-content":
            self.handle_file_content(query)
        elif path == "/api/daily-activity":
            self.handle_daily_activity()
        elif path == "/api/sync-log":
            self.handle_sync_log(query)
        elif path == "/api/retention-files":
            self.handle_retention_files(query)
        elif path == "/api/config/full":
            self.handle_config_full()
        elif path == "/api/config/folder/stignore":
            self.handle_get_stignore(query)
        else:
            self.send_response(404)
            self.end_headers()
            self.wfile.write(b"404 Not Found")

    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path

        if path == "/api/open-local":
            self.handle_open_local()
        elif path == "/api/restore":
            self.handle_restore()
        elif path == "/api/config/folder/add":
            self.handle_folder_add()
        elif path == "/api/config/folder/update":
            self.handle_folder_update()
        elif path == "/api/config/folder/rescan":
            self.handle_folder_rescan()
        elif path == "/api/config/folder/pause":
            self.handle_folder_pause()
        elif path == "/api/config/folder/stignore":
            self.handle_post_stignore()
        elif path == "/api/config/device/add":
            self.handle_device_add()
        elif path == "/api/config/device/update":
            self.handle_device_update()
        elif path == "/api/config/device/pause":
            self.handle_device_pause()
        elif path == "/api/system/restart":
            self.handle_system_restart()
        elif path == "/api/vpn/refresh":
            self.handle_vpn_refresh()
        else:
            self.send_response(404)
            self.end_headers()

    def do_DELETE(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        query = urllib.parse.parse_qs(url.query)

        if path == "/api/config/folder/delete":
            self.handle_folder_delete(query)
        elif path == "/api/config/device/delete":
            self.handle_device_delete(query)
        else:
            self.send_response(404)
            self.end_headers()

    # --- API Route Implementations ---

    def handle_overview(self, query):
        selected_fid = query.get("folder_id", [None])[0]
        folders = get_all_folders_config()
        devices = get_all_devices_config()
        conns = syncthing_req("/rest/system/connections") or {}
        
        # Pick target folder
        target_folder = None
        if selected_fid:
            for f in folders:
                if f.get('id') == selected_fid:
                    target_folder = f
                    break
        if not target_folder and folders:
            target_folder = folders[0]
            
        fid = target_folder.get('id') if target_folder else ""
        fpath = target_folder.get('path') if target_folder else ""
        flabel = target_folder.get('label') if target_folder else "Storage"
        
        # Folder cache
        with folder_cache_lock:
            cached = folder_cache.get(fid)
            if not cached and fpath:
                scan_single_folder(fid, fpath)
                cached = folder_cache.get(fid, {})
                
        cached_size = cached.get("total_size", 0) if cached else 0
        cached_files = cached.get("total_files", 0) if cached else 0

        # Syncthing status for folder
        st_folder = syncthing_req(f"/rest/db/status?folder={fid}") or {}
        global_files = st_folder.get("globalFiles", cached_files)
        global_bytes = st_folder.get("globalBytes", cached_size)
        state = st_folder.get("state", "idle")
        sync_percentage = 100
        if global_bytes > 0 and st_folder.get("inSyncBytes"):
            sync_percentage = min(100, round(st_folder["inSyncBytes"] / global_bytes * 100, 1))

        # Primary Remote Device info
        phone_conn = {}
        phone_dev = {}
        for d in devices:
            did = d.get('deviceID')
            if did and did in conns.get("connections", {}):
                phone_conn = conns["connections"][did]
                phone_dev = d
                break
        if not phone_dev and devices:
            for d in devices:
                if "spicykiller" not in d.get('name', '').lower():
                    phone_dev = d
                    break

        connected = phone_conn.get("connected", False)
        addr = phone_conn.get("address", "N/A")
        client_ver = phone_conn.get("clientVersion", "N/A")
        conn_mode = "Disconnected"
        if connected:
            if "192.168." in addr or "10." in addr:
                conn_mode = "Local Wi-Fi (LAN)"
            else:
                conn_mode = "Proton VPN Port P2P"

        # Retention stats
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT COUNT(*), COALESCE(SUM(file_size), 0) FROM retention_files WHERE status = 'in_retention'")
            ret_count, ret_size = cur.fetchone()
            
            today_str = datetime.now().strftime("%Y-%m-%d")
            cur.execute("SELECT bytes_synced, files_added, files_updated, files_deleted FROM daily_stats WHERE date = ?", (today_str,))
            today_row = cur.fetchone()
            conn.close()

        today_bytes = today_row["bytes_synced"] if today_row else 0
        today_added = today_row["files_added"] if today_row else 0
        today_deleted = today_row["files_deleted"] if today_row else 0

        # Read VPN Port status
        vpn_info = {"ip": "N/A", "port": "N/A", "active": False}
        if os.path.exists(VPN_PORT_FILE):
            try:
                with open(VPN_PORT_FILE, 'r') as vf:
                    for line in vf:
                        if line.startswith("IP="): vpn_info["ip"] = line.strip().split("=")[1]
                        elif line.startswith("PORT="): vpn_info["port"] = line.strip().split("=")[1]
                vpn_info["active"] = True
            except Exception:
                pass

        data = {
            "current_folder": {
                "id": fid,
                "label": flabel,
                "path": fpath,
                "state": state.capitalize(),
                "total_files": global_files,
                "total_bytes": global_bytes,
                "total_gb": format_folder_gb(global_bytes),
                "sync_percentage": sync_percentage,
                "paused": target_folder.get("paused", False) if target_folder else False
            },
            "all_folders": [{"id": f.get("id"), "label": f.get("label"), "path": f.get("path")} for f in folders],
            "device": {
                "name": phone_dev.get("name", "Android Phone"),
                "id": phone_dev.get("deviceID", ""),
                "connected": connected,
                "address": addr,
                "connection_mode": conn_mode,
                "client_version": client_ver,
                "paused": phone_dev.get("paused", False)
            },
            "retention": {
                "cleanout_days": DEFAULT_CLEANOUT_DAYS,
                "file_count": ret_count,
                "total_bytes": ret_size,
                "total_gb": format_folder_gb(ret_size),
                "total_mb": format_file_mb(ret_size)
            },
            "today": {
                "date": today_str,
                "bytes_synced": today_bytes,
                "bytes_mb": format_file_mb(today_bytes),
                "files_added": today_added,
                "files_deleted": today_deleted
            },
            "vpn": vpn_info
        }
        self.send_json(data)

    def handle_folders(self, query):
        selected_fid = query.get("folder_id", [None])[0]
        folders = get_all_folders_config()
        target = None
        for f in folders:
            if f.get('id') == selected_fid:
                target = f
                break
        if not target and folders:
            target = folders[0]
            
        fid = target.get('id') if target else ""
        fpath = target.get('path') if target else ""
        
        with folder_cache_lock:
            cached = folder_cache.get(fid)
            if not cached and fpath:
                scan_single_folder(fid, fpath)
                cached = folder_cache.get(fid, {})
                
        self.send_json({
            "folder_id": fid,
            "folder_label": target.get('label') if target else "",
            "folder_path": fpath,
            "folders": cached.get("folders", []) if cached else [],
            "total_size": cached.get("total_size", 0) if cached else 0,
            "total_size_gb": format_folder_gb(cached.get("total_size", 0)) if cached else "0.00 GB",
            "total_files": cached.get("total_files", 0) if cached else 0
        })

    def handle_browse(self, query):
        selected_fid = query.get("folder_id", [None])[0]
        subpath = query.get("subpath", [""])[0].strip("/\\")
        
        folders = get_all_folders_config()
        target = None
        for f in folders:
            if f.get('id') == selected_fid:
                target = f
                break
        if not target and folders:
            target = folders[0]
            
        if not target or not target.get('path'):
            self.send_json({"error": "Folder path not configured"}, status=400)
            return

        base_path = target.get('path')
        current_dir = os.path.abspath(os.path.join(base_path, subpath))
        
        # Traversal protection
        if not current_dir.startswith(os.path.abspath(base_path)):
            current_dir = os.path.abspath(base_path)
            subpath = ""

        if not os.path.exists(current_dir):
            self.send_json({"error": "Directory does not exist"}, status=404)
            return

        # Build breadcrumbs
        parts = subpath.split(os.sep) if subpath else []
        crumbs = [{"name": target.get('label', 'Root'), "subpath": ""}]
        accum = []
        for p in parts:
            if p:
                accum.append(p)
                crumbs.append({"name": p, "subpath": os.sep.join(accum)})

        items = []
        try:
            with os.scandir(current_dir) as it:
                for entry in it:
                    if entry.name.startswith(".st") or entry.name.startswith("."):
                        continue
                    item_rel = os.path.relpath(entry.path, base_path)
                    try:
                        st = entry.stat()
                        mtime_str = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
                        is_dir = entry.is_dir()
                        size_bytes = 0
                        if is_dir:
                            cached_folders = folder_cache.get(target.get('id'), {}).get('folders', [])
                            matched = next((f for f in cached_folders if f['name'] == entry.name), None)
                            if matched:
                                size_bytes = matched['size_bytes']
                            else:
                                try:
                                    d_size = 0
                                    for r, _, fs in os.walk(entry.path):
                                        for fl in fs:
                                            try: d_size += os.path.getsize(os.path.join(r, fl))
                                            except Exception: pass
                                    size_bytes = d_size
                                except Exception:
                                    size_bytes = 0
                        else:
                            size_bytes = st.st_size
                        
                        ext = os.path.splitext(entry.name)[1].lower()
                        media_type = "file"
                        if ext in ['.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.svg']:
                            media_type = "image"
                        elif ext in ['.mp4', '.mkv', '.mov', '.avi', '.webm']:
                            media_type = "video"
                        elif ext in ['.mp3', '.m4a', '.flac', '.wav', '.ogg']:
                            media_type = "audio"
                        elif ext in ['.pdf']:
                            media_type = "pdf"
                        elif ext in ['.txt', '.md', '.json', '.xml', '.py', '.sh', '.html', '.css', '.js']:
                            media_type = "text"

                        items.append({
                            "name": entry.name,
                            "is_dir": is_dir,
                            "subpath": item_rel,
                            "absolute_path": entry.path,
                            "size_bytes": size_bytes,
                            "size_mb": format_file_mb(size_bytes) if not is_dir else "-",
                            "size_gb": format_folder_gb(size_bytes) if is_dir else "-",
                            "mtime": mtime_str,
                            "media_type": media_type,
                            "ext": ext
                        })
                    except Exception:
                        pass
        except Exception as e:
            self.send_json({"error": str(e)}, status=500)
            return

        # Sort: directories first, then files
        items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

        parent_subpath = os.sep.join(parts[:-1]) if parts else None
        self.send_json({
            "folder_id": target.get('id'),
            "folder_label": target.get('label'),
            "current_subpath": subpath,
            "current_absolute_path": current_dir,
            "parent_subpath": parent_subpath,
            "breadcrumbs": crumbs,
            "items": items
        })

    def handle_file_content(self, query):
        selected_fid = query.get("folder_id", [None])[0]
        file_path = query.get("path", [""])[0].strip("/\\")
        
        folders = get_all_folders_config()
        target = None
        for f in folders:
            if f.get('id') == selected_fid:
                target = f
                break
        if not target:
            self.send_response(404)
            self.end_headers()
            return

        full_path = os.path.abspath(os.path.join(target.get('path', ''), file_path))
        if not full_path.startswith(os.path.abspath(target.get('path', ''))) or not os.path.isfile(full_path):
            self.send_response(404)
            self.end_headers()
            return

        mime_type, _ = mimetypes.guess_type(full_path)
        if not mime_type:
            mime_type = "application/octet-stream"

        try:
            size = os.path.getsize(full_path)
            self.send_response(200)
            self.send_header('Content-Type', mime_type)
            self.send_header('Content-Length', str(size))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            with open(full_path, 'rb') as f:
                shutil.copyfileobj(f, self.wfile)
        except Exception:
            pass

    def handle_open_local(self):
        req = self.parse_body_json()
        target = req.get("path")
        if not target and "folder_id" in req:
            fid = req.get("folder_id")
            for f in get_all_folders_config():
                if f.get('id') == fid:
                    target = f.get('path')
                    if req.get("subpath"):
                        target = os.path.join(target, req.get("subpath"))
                    break
        
        success, msg = open_local_path(target)
        self.send_json({"success": success, "message": msg}, status=200 if success else 400)

    def handle_daily_activity(self):
        today = date.today()
        dates = [(today - timedelta(days=i)).isoformat() for i in range(13, -1, -1)]
        
        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("SELECT date, bytes_synced, files_added, files_deleted FROM daily_stats WHERE date >= ?", (dates[0],))
            rows = {r["date"]: r for r in cur.fetchall()}
            conn.close()

        records = []
        for d in dates:
            r = rows.get(d)
            b = r["bytes_synced"] if r else 0
            mb = round(b / (1024 * 1024), 2)
            fa = r["files_added"] if r else 0
            fd = r["files_deleted"] if r else 0
            records.append({
                "date": d,
                "bytes": b,
                "size_mb": mb,
                "size_formatted": format_file_mb(b),
                "files_added": fa,
                "files_deleted": fd
            })
        self.send_json({"activity": records})

    def handle_sync_log(self, query):
        limit = int(query.get("limit", [50])[0])
        action = query.get("action", [None])[0]
        folder = query.get("folder", [None])[0]
        search = query.get("search", [None])[0]

        sql = "SELECT id, syncthing_event_id, timestamp, folder_id, device_name, item_path, root_folder, action, file_size FROM events WHERE 1=1"
        params = []

        if action and action != "all":
            sql += " AND action = ?"
            params.append(action)
        if folder and folder != "all":
            sql += " AND root_folder = ?"
            params.append(folder)
        if search:
            sql += " AND item_path LIKE ?"
            params.append(f"%{search}%")

        sql += " ORDER BY syncthing_event_id DESC LIMIT ?"
        params.append(limit)

        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "timestamp": r["timestamp"][:19].replace("T", " ") if r["timestamp"] else "",
                "device_name": r["device_name"],
                "item_path": r["item_path"],
                "root_folder": r["root_folder"],
                "action": r["action"],
                "size_bytes": r["file_size"],
                "size_mb": format_file_mb(r["file_size"])
            })
        self.send_json({"items": results})

    def handle_retention_files(self, query):
        scan_all_retention_vaults()
        folder = query.get("folder", [None])[0]
        search = query.get("search", [None])[0]
        sort_by = query.get("sort", ["days_asc"])[0]

        sql = "SELECT id, relative_path, file_name, root_folder, folder_id, absolute_path, file_size, deleted_timestamp, days_remaining, status FROM retention_files WHERE status = 'in_retention'"
        params = []

        if folder and folder != "all":
            sql += " AND root_folder = ?"
            params.append(folder)
        if search:
            sql += " AND (relative_path LIKE ? OR file_name LIKE ?)"
            params.extend([f"%{search}%", f"%{search}%"])

        if sort_by == "days_asc": sql += " ORDER BY days_remaining ASC"
        elif sort_by == "days_desc": sql += " ORDER BY days_remaining DESC"
        elif sort_by == "size_desc": sql += " ORDER BY file_size DESC"
        elif sort_by == "newest": sql += " ORDER BY mtime_epoch DESC"
        else: sql += " ORDER BY days_remaining ASC"

        with db_lock:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute(sql, params)
            rows = cur.fetchall()
            conn.close()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "relative_path": r["relative_path"],
                "file_name": r["file_name"],
                "root_folder": r["root_folder"],
                "folder_id": r["folder_id"],
                "absolute_path": r["absolute_path"],
                "size_bytes": r["file_size"],
                "size_mb": format_file_mb(r["file_size"]),
                "deleted_timestamp": r["deleted_timestamp"],
                "days_remaining": r["days_remaining"],
                "status": r["status"]
            })
        self.send_json({"files": results})

    def handle_restore(self):
        req = self.parse_body_json()
        rel_path = req.get("path")
        fid = req.get("folder_id")
        
        folders = get_all_folders_config()
        target_folder = None
        for f in folders:
            if not fid or f.get('id') == fid:
                target_folder = f
                break

        if not target_folder:
            self.send_json({"success": False, "error": "Folder not found"}, status=400)
            return

        storage_root = target_folder.get('path')
        ver = target_folder.get('versioning', {})
        ret_root = ver.get('fsPath') or os.path.join(storage_root, '.stversions')

        source = os.path.join(ret_root, rel_path)
        target = os.path.join(storage_root, rel_path)

        if not os.path.exists(source):
            self.send_json({"success": False, "error": "Source file not found in retention vault"}, status=404)
            return

        try:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            shutil.move(source, target)
            with db_lock:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("UPDATE retention_files SET status = 'restored' WHERE relative_path = ?", (rel_path,))
                conn.commit()
                conn.close()
            scan_all_retention_vaults()
            self.send_json({"success": True, "message": f"Successfully restored {os.path.basename(rel_path)} to active storage"})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, status=500)

    # --- Full Configuration Center Handlers ---

    def handle_config_full(self):
        folders = get_all_folders_config()
        devices = get_all_devices_config()
        sys_status = syncthing_req("/rest/system/status") or {}
        conns = syncthing_req("/rest/system/connections") or {}
        
        # Enrich devices with live connection status
        for d in devices:
            did = d.get('deviceID')
            c = conns.get("connections", {}).get(did, {})
            d["is_connected"] = c.get("connected", False)
            d["address"] = c.get("address", "N/A")
            d["in_bytes"] = c.get("inBytesTotal", 0)
            d["out_bytes"] = c.get("outBytesTotal", 0)
            d["client_version"] = c.get("clientVersion", "N/A")
            d["in_formatted"] = format_bytes_auto(d["in_bytes"])
            d["out_formatted"] = format_bytes_auto(d["out_bytes"])

        # Enrich folders with sizes
        for f in folders:
            fid = f.get('id')
            with folder_cache_lock:
                cached = folder_cache.get(fid)
            f["size_gb"] = format_folder_gb(cached.get("total_size", 0)) if cached else "N/A"
            f["total_files"] = cached.get("total_files", 0) if cached else "N/A"

        self.send_json({
            "folders": folders,
            "devices": devices,
            "system": sys_status
        })

    def handle_folder_add(self):
        req = self.parse_body_json()
        fid = req.get("id")
        label = req.get("label") or fid
        path = req.get("path")
        ftype = req.get("type", "sendreceive")
        cleanout = req.get("cleanout_days", DEFAULT_CLEANOUT_DAYS)
        dev_ids = req.get("devices", [])

        if not fid or not path:
            self.send_json({"success": False, "error": "Folder ID and Path are required"}, status=400)
            return

        folder_obj = {
            "id": fid,
            "label": label,
            "filesystemType": "basic",
            "path": path,
            "type": ftype,
            "devices": [{"deviceID": did} for did in dev_ids],
            "rescanIntervalS": 3600,
            "fsWatcherEnabled": True,
            "fsWatcherDelayS": 10,
            "ignorePerms": False,
            "autoNormalize": True,
            "versioning": {
                "type": "trashcan",
                "params": {"cleanoutDays": str(cleanout)},
                "cleanupIntervalS": 3600,
                "fsPath": "/media/spicykiller/New Volume/Syncthing/Retention_60Days",
                "fsType": "basic"
            }
        }
        res = syncthing_req(f"/rest/config/folders/{fid}", method='PUT', data=folder_obj)
        if res:
            os.makedirs(path, exist_ok=True)
            self.send_json({"success": True, "message": f"Folder {label} created successfully"})
        else:
            self.send_json({"success": False, "error": "Failed to create folder in Syncthing"}, status=500)

    def handle_folder_update(self):
        req = self.parse_body_json()
        fid = req.get("id")
        if not fid:
            self.send_json({"success": False, "error": "Folder ID required"}, status=400)
            return

        current = syncthing_req(f"/rest/config/folders/{fid}")
        if not current:
            self.send_json({"success": False, "error": "Folder not found"}, status=404)
            return

        if "label" in req: current["label"] = req["label"]
        if "path" in req: current["path"] = req["path"]
        if "type" in req: current["type"] = req["type"]
        if "paused" in req: current["paused"] = req["paused"]
        if "cleanout_days" in req:
            current["versioning"]["params"]["cleanoutDays"] = str(req["cleanout_days"])

        res = syncthing_req(f"/rest/config/folders/{fid}", method='PUT', data=current)
        self.send_json({"success": bool(res), "message": "Folder updated successfully"})

    def handle_folder_rescan(self):
        req = self.parse_body_json()
        fid = req.get("folder_id")
        url = f"/rest/db/scan?folder={fid}" if fid else "/rest/db/scan"
        res = syncthing_req(url, method='POST')
        self.send_json({"success": True, "message": f"Scan triggered for {fid or 'all folders'}"})

    def handle_folder_pause(self):
        req = self.parse_body_json()
        fid = req.get("folder_id")
        pause = req.get("paused", True)
        current = syncthing_req(f"/rest/config/folders/{fid}")
        if current:
            current["paused"] = pause
            syncthing_req(f"/rest/config/folders/{fid}", method='PUT', data=current)
            self.send_json({"success": True, "message": f"Folder {'paused' if pause else 'resumed'}"})
        else:
            self.send_json({"success": False, "error": "Folder not found"}, status=404)

    def handle_folder_delete(self, query):
        fid = query.get("id", [None])[0]
        if not fid:
            self.send_json({"success": False, "error": "Folder ID required"}, status=400)
            return
        res = syncthing_req(f"/rest/config/folders/{fid}", method='DELETE')
        self.send_json({"success": True, "message": f"Folder {fid} removed from Syncthing"})

    def handle_get_stignore(self, query):
        fid = query.get("folder_id", [None])[0]
        data = syncthing_req(f"/rest/db/ignores?folder={fid}") or {}
        ignores = data.get("ignore", []) or []
        self.send_json({"folder_id": fid, "content": "\n".join(ignores)})

    def handle_post_stignore(self):
        req = self.parse_body_json()
        fid = req.get("folder_id")
        content = req.get("content", "")
        lines = [l.strip() for l in content.split("\n") if l.strip()]
        res = syncthing_req(f"/rest/db/ignores?folder={fid}", method='POST', data={"ignore": lines})
        self.send_json({"success": True, "message": ".stignore updated successfully"})

    def handle_device_add(self):
        req = self.parse_body_json()
        did = req.get("deviceID")
        name = req.get("name") or "New Device"
        addresses = req.get("addresses") or ["dynamic"]
        if not did:
            self.send_json({"success": False, "error": "Device ID is required"}, status=400)
            return

        dev_obj = {
            "deviceID": did,
            "name": name,
            "addresses": addresses if isinstance(addresses, list) else [addresses],
            "compression": "metadata",
            "introducer": False,
            "paused": False,
            "autoAcceptFolders": True
        }
        res = syncthing_req(f"/rest/config/devices/{did}", method='PUT', data=dev_obj)
        self.send_json({"success": bool(res), "message": f"Device {name} paired successfully"})

    def handle_device_update(self):
        req = self.parse_body_json()
        did = req.get("deviceID")
        current = syncthing_req(f"/rest/config/devices/{did}")
        if not current:
            self.send_json({"success": False, "error": "Device not found"}, status=404)
            return

        if "name" in req: current["name"] = req["name"]
        if "addresses" in req:
            addrs = req["addresses"]
            current["addresses"] = addrs if isinstance(addrs, list) else [a.strip() for a in addrs.split(",") if a.strip()]
        if "paused" in req: current["paused"] = req["paused"]

        res = syncthing_req(f"/rest/config/devices/{did}", method='PUT', data=current)
        self.send_json({"success": bool(res), "message": "Device updated successfully"})

    def handle_device_pause(self):
        req = self.parse_body_json()
        did = req.get("deviceID")
        pause = req.get("paused", True)
        current = syncthing_req(f"/rest/config/devices/{did}")
        if current:
            current["paused"] = pause
            syncthing_req(f"/rest/config/devices/{did}", method='PUT', data=current)
            self.send_json({"success": True, "message": f"Device {'paused' if pause else 'resumed'}"})
        else:
            self.send_json({"success": False, "error": "Device not found"}, status=404)

    def handle_device_delete(self, query):
        did = query.get("id", [None])[0]
        if not did:
            self.send_json({"success": False, "error": "Device ID required"}, status=400)
            return
        syncthing_req(f"/rest/config/devices/{did}", method='DELETE')
        self.send_json({"success": True, "message": f"Device {did} unpaired"})

    def handle_system_restart(self):
        syncthing_req("/rest/system/restart", method='POST')
        self.send_json({"success": True, "message": "Syncthing restart command dispatched"})

    def handle_vpn_refresh(self):
        try:
            subprocess.run(["systemctl", "--user", "restart", "proton-natpmp.service"], timeout=5)
            self.send_json({"success": True, "message": "Proton VPN port forwarder lease restarted"})
        except Exception as e:
            self.send_json({"success": False, "error": str(e)}, status=500)

    def handle_dashboard_ui(self):
        html = DASHBOARD_HTML.encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(html)))
        self.end_headers()
        self.wfile.write(html)

# Embedded Single-Page Application (HTML/JS/Tailwind)
DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="en" class="dark">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Syncthing Advanced Hub & Control Center</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
  <script>
    tailwind.config = {
      darkMode: 'class',
      theme: {
        extend: {
          colors: {
            brand: { 50: '#eef2ff', 500: '#6366f1', 600: '#4f46e5', 700: '#4338ca' },
            darkbg: '#090d16',
            cardbg: '#101625',
            cardborder: '#1e293b'
          }
        }
      }
    }
  </script>
  <style>
    body { background-color: #090d16; color: #f1f5f9; font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; }
    .badge-safe { background: rgba(16, 185, 129, 0.15); color: #34d399; border: 1px solid rgba(16, 185, 129, 0.3); }
    .badge-warning { background: rgba(245, 158, 11, 0.15); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.3); }
    .badge-danger { background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid rgba(239, 68, 68, 0.3); }
    .badge-blue { background: rgba(99, 102, 241, 0.15); color: #818cf8; border: 1px solid rgba(99, 102, 241, 0.3); }
    ::-webkit-scrollbar { width: 8px; height: 8px; }
    ::-webkit-scrollbar-track { background: #090d16; }
    ::-webkit-scrollbar-thumb { background: #1e293b; border-radius: 4px; }
    ::-webkit-scrollbar-thumb:hover { background: #334155; }
  </style>
</head>
<body class="min-h-screen flex flex-col">

  <!-- Toast Container -->
  <div id="toast" class="fixed bottom-6 right-6 z-50 transform transition-all duration-300 translate-y-24 opacity-0 pointer-events-none flex items-center px-4 py-3 rounded-xl shadow-2xl bg-gray-900 border border-gray-700 text-white space-x-3">
    <div id="toast-icon"></div>
    <div id="toast-msg" class="text-sm font-medium"></div>
  </div>

  <!-- Media Preview Modal -->
  <div id="preview-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm hidden flex items-center justify-center p-4">
    <div class="bg-cardbg border border-cardborder rounded-2xl max-w-4xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl">
      <div class="px-5 py-3 border-b border-cardborder flex items-center justify-between">
        <h3 id="preview-title" class="text-sm font-bold text-white truncate max-w-lg">Preview</h3>
        <button onclick="closePreviewModal()" class="text-gray-400 hover:text-white p-1 rounded-lg hover:bg-gray-800">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
        </button>
      </div>
      <div id="preview-body" class="flex-1 overflow-auto p-4 flex items-center justify-center min-h-[300px]"></div>
      <div class="px-5 py-3 border-t border-cardborder bg-gray-900/50 flex justify-end gap-3">
        <button id="preview-open-laptop-btn" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700">Open on Laptop</button>
        <a id="preview-download-link" download class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white">Download</a>
      </div>
    </div>
  </div>

  <!-- General Settings / Edit Modal -->
  <div id="settings-modal" class="fixed inset-0 z-50 bg-black/80 backdrop-blur-sm hidden flex items-center justify-center p-4">
    <div class="bg-cardbg border border-cardborder rounded-2xl max-w-2xl w-full max-h-[90vh] flex flex-col overflow-hidden shadow-2xl">
      <div class="px-5 py-4 border-b border-cardborder flex items-center justify-between">
        <h3 id="settings-modal-title" class="text-base font-bold text-white">Settings</h3>
        <button onclick="closeSettingsModal()" class="text-gray-400 hover:text-white p-1 rounded-lg hover:bg-gray-800">
          <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg>
        </button>
      </div>
      <div id="settings-modal-body" class="p-6 overflow-y-auto space-y-4"></div>
      <div class="px-6 py-4 border-t border-cardborder bg-gray-900/50 flex justify-end gap-3" id="settings-modal-footer"></div>
    </div>
  </div>

  <!-- Top Navigation Bar -->
  <header class="border-b border-cardborder bg-cardbg/80 backdrop-blur sticky top-0 z-40">
    <div class="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8 py-3.5 flex flex-wrap items-center justify-between gap-4">
      
      <!-- Brand & Folder Switcher -->
      <div class="flex items-center space-x-4">
        <div class="w-10 h-10 rounded-xl bg-gradient-to-tr from-brand-600 to-indigo-500 flex items-center justify-center shadow-lg shadow-brand-500/25">
          <svg class="w-6 h-6 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
          </svg>
        </div>
        <div>
          <div class="flex items-center gap-2">
            <h1 class="text-base font-extrabold tracking-tight text-white">Syncthing Control Hub</h1>
            <span class="text-xs px-2 py-0.5 rounded-full font-bold bg-brand-500/20 text-brand-400 border border-brand-500/30">Pro</span>
          </div>
          <div class="flex items-center gap-2 mt-0.5">
            <span class="text-xs text-gray-400">Folder:</span>
            <select id="global-folder-select" onchange="onFolderChange(this.value)" class="bg-gray-900 text-xs text-indigo-300 font-semibold border border-cardborder rounded-lg px-2 py-0.5 focus:outline-none focus:border-brand-500"></select>
          </div>
        </div>
      </div>

      <!-- Quick Action Buttons & Status -->
      <div class="flex items-center space-x-3">
        <!-- Open Sync Root on Laptop Button -->
        <button onclick="openCurrentFolderOnLaptop()" class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-indigo-300 hover:text-white border border-gray-700 transition shadow">
          <svg class="w-4 h-4 text-indigo-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 19a2 2 0 01-2-2V7a2 2 0 012-2h4l2 2h4a2 2 0 012 2v1M5 19h14a2 2 0 002-2v-5a2 2 0 00-2-2H9a2 2 0 00-2 2v5a2 2 0 01-2 2z"></path>
          </svg>
          📂 Open on Laptop
        </button>

        <!-- Live Connection Pill -->
        <div id="status-pill" class="flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs font-medium border bg-gray-800 text-gray-400 border-gray-700">
          <span class="w-2 h-2 rounded-full bg-gray-500 animate-pulse"></span>
          <span id="status-text">Checking device...</span>
        </div>

        <!-- Manual Refresh -->
        <button onclick="refreshAll()" title="Refresh All Data" class="p-2 rounded-lg bg-gray-800 hover:bg-gray-700 border border-gray-700 text-gray-300 hover:text-white transition">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path>
          </svg>
        </button>
      </div>

    </div>
  </header>

  <!-- Main Content Container -->
  <main class="flex-1 max-w-7xl w-full mx-auto px-4 sm:px-6 lg:px-8 py-6 space-y-6">

    <!-- KPI Summary Cards (GB for folders, MB for files) -->
    <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      
      <!-- Card 1: Active Synced Folder Storage (Formatted in GB) -->
      <div class="bg-cardbg border border-cardborder rounded-2xl p-5 shadow-lg relative overflow-hidden group">
        <div class="flex justify-between items-start">
          <div>
            <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Active Folder Storage</p>
            <h3 id="kpi-total-storage" class="text-2xl font-black text-white mt-1">-- GB</h3>
            <p id="kpi-total-files" class="text-xs text-gray-400 mt-0.5">-- files in folder</p>
          </div>
          <div class="p-3 bg-indigo-500/10 rounded-xl text-indigo-400">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path>
            </svg>
          </div>
        </div>
        <div class="mt-4 flex items-center justify-between text-xs">
          <span class="text-emerald-400 font-medium flex items-center gap-1">
            <span class="w-1.5 h-1.5 rounded-full bg-emerald-400"></span>
            <span id="kpi-sync-state">Idle</span>
          </span>
          <button onclick="openCurrentFolderOnLaptop()" class="text-xs text-indigo-400 hover:text-indigo-300 font-semibold underline">Open Folder ↗</button>
        </div>
        <div class="w-full bg-gray-800 rounded-full h-1.5 mt-2">
          <div id="kpi-sync-bar" class="bg-indigo-500 h-1.5 rounded-full transition-all duration-500" style="width: 100%"></div>
        </div>
      </div>

      <!-- Card 2: 60-Day Retention Holding (Vault) -->
      <div class="bg-cardbg border border-cardborder rounded-2xl p-5 shadow-lg relative overflow-hidden">
        <div class="flex justify-between items-start">
          <div>
            <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">60-Day Retention Vault</p>
            <h3 id="kpi-retention-count" class="text-2xl font-black text-emerald-400 mt-1">0 files</h3>
            <p id="kpi-retention-size" class="text-xs text-gray-400 mt-0.5">0.00 GB preserved on laptop</p>
          </div>
          <div class="p-3 bg-emerald-500/10 rounded-xl text-emerald-400">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"></path>
            </svg>
          </div>
        </div>
        <div class="mt-4 flex items-center justify-between text-xs text-gray-400">
          <span>Auto-purge timer:</span>
          <button onclick="openLocalPath('/media/spicykiller/New Volume/Syncthing/Retention_60Days')" class="text-emerald-400 hover:text-emerald-300 font-semibold underline">Open Vault ↗</button>
        </div>
      </div>

      <!-- Card 3: Today's Sync Activity (Formatted in MB) -->
      <div class="bg-cardbg border border-cardborder rounded-2xl p-5 shadow-lg relative overflow-hidden">
        <div class="flex justify-between items-start">
          <div>
            <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Today's Data Synced</p>
            <h3 id="kpi-today-volume" class="text-2xl font-black text-white mt-1">-- MB</h3>
            <p id="kpi-today-files" class="text-xs text-gray-400 mt-0.5">-- files transferred</p>
          </div>
          <div class="p-3 bg-blue-500/10 rounded-xl text-blue-400">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6"></path>
            </svg>
          </div>
        </div>
        <div class="mt-4 flex items-center justify-between text-xs text-gray-400">
          <span>Deleted today on phone:</span>
          <span id="kpi-today-deleted" class="text-rose-400 font-bold font-mono">0</span>
        </div>
      </div>

      <!-- Card 4: Device & Tunnel Status -->
      <div class="bg-cardbg border border-cardborder rounded-2xl p-5 shadow-lg relative overflow-hidden">
        <div class="flex justify-between items-start">
          <div>
            <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Connected Device</p>
            <h3 id="kpi-device-name" class="text-xl font-bold text-white mt-1">Nothing Phone 3A</h3>
            <p id="kpi-device-ip" class="text-xs text-gray-400 mt-0.5 font-mono">--</p>
          </div>
          <div class="p-3 bg-purple-500/10 rounded-xl text-purple-400">
            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 18h.01M8 21h8a2 2 0 002-2V5a2 2 0 00-2-2H8a2 2 0 00-2 2v14a2 2 0 002 2z"></path>
            </svg>
          </div>
        </div>
        <div class="mt-4 flex items-center justify-between text-xs">
          <span class="text-gray-400">Proton Port:</span>
          <span id="kpi-vpn-port" class="px-2 py-0.5 rounded text-xs font-semibold bg-gray-800 text-indigo-300 font-mono">57417</span>
        </div>
      </div>

    </div>

    <!-- Charts Section -->
    <div class="grid grid-cols-1 lg:grid-cols-3 gap-6">
      
      <!-- Chart 1: Daily Sync Activity -->
      <div class="lg:col-span-2 bg-cardbg border border-cardborder rounded-2xl p-5 shadow-lg">
        <div class="flex items-center justify-between mb-4">
          <div>
            <h2 class="text-base font-bold text-white">Daily Sync Activity</h2>
            <p class="text-xs text-gray-400">Daily transfer volume in MB and files synced/deleted (last 14 days)</p>
          </div>
          <span class="text-xs font-mono px-2 py-1 rounded bg-gray-800 text-gray-300 border border-gray-700">Live Volume</span>
        </div>
        <div class="h-64">
          <canvas id="dailySyncChart"></canvas>
        </div>
      </div>

      <!-- Chart 2: Folder Breakdown Donut -->
      <div class="bg-cardbg border border-cardborder rounded-2xl p-5 shadow-lg flex flex-col">
        <div class="flex items-center justify-between mb-2">
          <div>
            <h2 class="text-base font-bold text-white">Storage Share (GB)</h2>
            <p class="text-xs text-gray-400">Directory breakdown in GB</p>
          </div>
        </div>
        <div class="flex-1 flex items-center justify-center relative min-h-[200px]">
          <canvas id="folderDonutChart"></canvas>
        </div>
      </div>

    </div>

    <!-- Navigation Tabs -->
    <div class="bg-cardbg border border-cardborder rounded-2xl shadow-lg overflow-hidden">
      
      <!-- Tab Headers -->
      <div class="flex border-b border-cardborder bg-gray-900/60 px-4 sm:px-6 overflow-x-auto">
        <button onclick="switchTab('explorer')" id="tab-btn-explorer" class="py-4 px-4 font-bold text-sm border-b-2 border-brand-500 text-brand-400 flex items-center gap-2 whitespace-nowrap">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7v10a2 2 0 002 2h14a2 2 0 002-2V9a2 2 0 00-2-2h-6l-2-2H5a2 2 0 00-2 2z"></path></svg>
          📁 File Explorer & Inspector
        </button>

        <button onclick="switchTab('retention')" id="tab-btn-retention" class="py-4 px-4 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-gray-200 flex items-center gap-2 whitespace-nowrap">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"></path></svg>
          🛡️ 60-Day Retention Vault
          <span id="tab-retention-badge" class="ml-1 px-2 py-0.5 rounded-full text-xs bg-emerald-500/20 text-emerald-300">0</span>
        </button>

        <button onclick="switchTab('synclog')" id="tab-btn-synclog" class="py-4 px-4 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-gray-200 flex items-center gap-2 whitespace-nowrap">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
          ⚡ Live Sync Log
        </button>

        <button onclick="switchTab('folders')" id="tab-btn-folders" class="py-4 px-4 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-gray-200 flex items-center gap-2 whitespace-nowrap">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z"></path></svg>
          📊 Folder Analytics (GB)
        </button>

        <button onclick="switchTab('control')" id="tab-btn-control" class="py-4 px-4 font-bold text-sm border-b-2 border-transparent text-gray-400 hover:text-gray-200 flex items-center gap-2 whitespace-nowrap">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z"></path><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z"></path></svg>
          ⚙️ Settings & Control Center
        </button>
      </div>

      <!-- Tab 1: Interactive File Explorer (See Actual Files & Open on Laptop) -->
      <div id="tab-pane-explorer" class="p-5 sm:p-6 space-y-4">
        
        <!-- Explorer Header & Breadcrumbs -->
        <div class="flex flex-wrap items-center justify-between gap-3 bg-gray-900/80 p-3.5 rounded-xl border border-cardborder">
          <div class="flex items-center gap-2 flex-wrap text-sm" id="explorer-breadcrumbs">
            <!-- Dynamically populated breadcrumbs -->
          </div>
          <div class="flex items-center gap-2">
            <button onclick="openCurrentExplorerDirOnLaptop()" class="flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white transition shadow">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 19a2 2 0 01-2-2V7a2 2 0 012-2h4l2 2h4a2 2 0 012 2v1M5 19h14a2 2 0 002-2v-5a2 2 0 00-2-2H9a2 2 0 00-2 2v5a2 2 0 01-2 2z"></path></svg>
              📂 Open This Folder on Laptop
            </button>
            <button onclick="browseToSubpath(currentBrowseSubpath)" class="p-1.5 rounded-lg bg-gray-800 hover:bg-gray-700 text-gray-300">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"></path></svg>
            </button>
          </div>
        </div>

        <!-- Files & Directories Table -->
        <div class="overflow-x-auto border border-cardborder rounded-xl">
          <table class="w-full text-left text-sm text-gray-300">
            <thead class="bg-gray-900/80 text-xs uppercase text-gray-400 border-b border-cardborder">
              <tr>
                <th class="py-3 px-4">Item Name</th>
                <th class="py-3 px-4">Type</th>
                <th class="py-3 px-4">Size (Files in MB / Folders in GB)</th>
                <th class="py-3 px-4">Last Modified</th>
                <th class="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody id="explorer-tbody" class="divide-y divide-cardborder font-normal">
              <!-- Dynamically populated -->
            </tbody>
          </table>
        </div>

      </div>

      <!-- Tab 2: 60-Day Retention Vault -->
      <div id="tab-pane-retention" class="p-5 sm:p-6 space-y-4 hidden">
        <div class="rounded-xl p-4 bg-emerald-950/20 border border-emerald-800/30 flex items-start gap-3">
          <div class="p-2 rounded-lg bg-emerald-500/10 text-emerald-400 mt-0.5">
            <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z"></path></svg>
          </div>
          <div class="text-xs text-gray-300 leading-relaxed">
            <strong class="text-white font-semibold">60-Day Safety Policy:</strong> Files deleted from your phone are retained here for <strong>60 days</strong> before automatic permanent deletion. They are <strong>never synced back to the phone</strong> unless you click <strong>Restore</strong>.
          </div>
        </div>

        <!-- Filters & Search -->
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-2 flex-1 min-w-[240px]">
            <input type="text" id="retention-search" oninput="loadRetentionFiles()" placeholder="Search deleted files or folders..." class="w-full bg-gray-900 border border-cardborder rounded-xl px-4 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-emerald-500">
          </div>
          <div class="flex items-center gap-2">
            <select id="retention-folder-filter" onchange="loadRetentionFiles()" class="bg-gray-900 border border-cardborder rounded-xl px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-emerald-500">
              <option value="all">All Folders</option>
            </select>
            <select id="retention-sort" onchange="loadRetentionFiles()" class="bg-gray-900 border border-cardborder rounded-xl px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-emerald-500">
              <option value="days_asc">Days Left (Fewest First)</option>
              <option value="days_desc">Days Left (Most First)</option>
              <option value="newest">Recently Deleted</option>
              <option value="size_desc">File Size (Largest First)</option>
            </select>
          </div>
        </div>

        <!-- Retention Table -->
        <div class="overflow-x-auto border border-cardborder rounded-xl">
          <table class="w-full text-left text-sm text-gray-300">
            <thead class="bg-gray-900/80 text-xs uppercase text-gray-400 border-b border-cardborder">
              <tr>
                <th class="py-3 px-4">File Name & Path</th>
                <th class="py-3 px-4">Folder</th>
                <th class="py-3 px-4">File Size (MB)</th>
                <th class="py-3 px-4">Deleted On</th>
                <th class="py-3 px-4">Days Remaining</th>
                <th class="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody id="retention-tbody" class="divide-y divide-cardborder font-normal">
              <!-- Dynamically populated -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 3: Live Sync Log -->
      <div id="tab-pane-synclog" class="p-5 sm:p-6 space-y-4 hidden">
        <div class="flex flex-wrap items-center justify-between gap-3">
          <div class="flex items-center gap-2 flex-1 min-w-[240px]">
            <input type="text" id="synclog-search" oninput="loadSyncLog()" placeholder="Search added or synced files..." class="w-full bg-gray-900 border border-cardborder rounded-xl px-4 py-2 text-sm text-gray-200 placeholder-gray-500 focus:outline-none focus:border-brand-500">
          </div>
          <div class="flex items-center gap-2">
            <select id="synclog-action-filter" onchange="loadSyncLog()" class="bg-gray-900 border border-cardborder rounded-xl px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-brand-500">
              <option value="all">All Actions</option>
              <option value="update">Added / Synced</option>
              <option value="delete">Deleted on Phone</option>
            </select>
            <select id="synclog-folder-filter" onchange="loadSyncLog()" class="bg-gray-900 border border-cardborder rounded-xl px-3 py-2 text-sm text-gray-200 focus:outline-none focus:border-brand-500">
              <option value="all">All Folders</option>
            </select>
          </div>
        </div>

        <div class="overflow-x-auto border border-cardborder rounded-xl">
          <table class="w-full text-left text-sm text-gray-300">
            <thead class="bg-gray-900/80 text-xs uppercase text-gray-400 border-b border-cardborder">
              <tr>
                <th class="py-3 px-4">Timestamp</th>
                <th class="py-3 px-4">Action</th>
                <th class="py-3 px-4">File Path</th>
                <th class="py-3 px-4">Folder</th>
                <th class="py-3 px-4">File Size (MB)</th>
                <th class="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody id="synclog-tbody" class="divide-y divide-cardborder font-normal">
              <!-- Dynamically populated -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 4: Folder Analytics (GB) -->
      <div id="tab-pane-folders" class="p-5 sm:p-6 space-y-4 hidden">
        <div class="overflow-x-auto border border-cardborder rounded-xl">
          <table class="w-full text-left text-sm text-gray-300">
            <thead class="bg-gray-900/80 text-xs uppercase text-gray-400 border-b border-cardborder">
              <tr>
                <th class="py-3 px-4">Folder Name</th>
                <th class="py-3 px-4">Item Count</th>
                <th class="py-3 px-4">Total Size (GB)</th>
                <th class="py-3 px-4">Storage Share</th>
                <th class="py-3 px-4 text-right">Actions</th>
              </tr>
            </thead>
            <tbody id="folders-tbody" class="divide-y divide-cardborder font-normal">
              <!-- Dynamically populated -->
            </tbody>
          </table>
        </div>
      </div>

      <!-- Tab 5: Settings & Control Center (Complete Control) -->
      <div id="tab-pane-control" class="p-5 sm:p-6 space-y-6 hidden">
        
        <!-- Section A: Folder Management -->
        <div class="space-y-3">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="text-base font-bold text-white flex items-center gap-2">
                📁 Folders Configuration
                <span class="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400 font-mono" id="control-folders-count">0</span>
              </h3>
              <p class="text-xs text-gray-400">All synced directories on laptop storage with versioning and ignore patterns</p>
            </div>
            <button onclick="openAddFolderModal()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white flex items-center gap-1.5 shadow">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>
              Add New Folder
            </button>
          </div>
          <div class="overflow-x-auto border border-cardborder rounded-xl">
            <table class="w-full text-left text-sm text-gray-300">
              <thead class="bg-gray-900/80 text-xs uppercase text-gray-400 border-b border-cardborder">
                <tr>
                  <th class="py-3 px-4">Folder Label & ID</th>
                  <th class="py-3 px-4">Local Laptop Path</th>
                  <th class="py-3 px-4">Type</th>
                  <th class="py-3 px-4">Size (GB)</th>
                  <th class="py-3 px-4">Status</th>
                  <th class="py-3 px-4 text-right">Settings & Actions</th>
                </tr>
              </thead>
              <tbody id="control-folders-tbody" class="divide-y divide-cardborder"></tbody>
            </table>
          </div>
        </div>

        <!-- Section B: Device Management -->
        <div class="space-y-3 pt-4 border-t border-cardborder">
          <div class="flex items-center justify-between">
            <div>
              <h3 class="text-base font-bold text-white flex items-center gap-2">
                📱 Connected Devices
                <span class="text-xs px-2 py-0.5 rounded bg-gray-800 text-gray-400 font-mono" id="control-devices-count">0</span>
              </h3>
              <p class="text-xs text-gray-400">Pair new phones, manage sync conditions, and configure peer addresses</p>
            </div>
            <button onclick="openAddDeviceModal()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white flex items-center gap-1.5 shadow">
              <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"></path></svg>
              Add New Device
            </button>
          </div>
          <div class="overflow-x-auto border border-cardborder rounded-xl">
            <table class="w-full text-left text-sm text-gray-300">
              <thead class="bg-gray-900/80 text-xs uppercase text-gray-400 border-b border-cardborder">
                <tr>
                  <th class="py-3 px-4">Device Name & ID</th>
                  <th class="py-3 px-4">Address / Mode</th>
                  <th class="py-3 px-4">Transfer Stats</th>
                  <th class="py-3 px-4">State</th>
                  <th class="py-3 px-4 text-right">Actions</th>
                </tr>
              </thead>
              <tbody id="control-devices-tbody" class="divide-y divide-cardborder"></tbody>
            </table>
          </div>
        </div>

        <!-- Section C: System Operations & Engine Control -->
        <div class="space-y-3 pt-4 border-t border-cardborder">
          <div>
            <h3 class="text-base font-bold text-white">⚡ System Operations & Engine Control</h3>
            <p class="text-xs text-gray-400">Core engine restarts, full rescans, and VPN port forwarding lease management</p>
          </div>
          <div class="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div class="bg-gray-900/60 border border-cardborder p-4 rounded-xl flex items-center justify-between">
              <div>
                <p class="text-xs font-semibold text-white">Restart Syncthing Engine</p>
                <p class="text-xs text-gray-400 mt-0.5">Dispatches safe restart command</p>
              </div>
              <button onclick="restartSyncthing()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-rose-600 hover:bg-rose-500 text-white">Restart Engine</button>
            </div>

            <div class="bg-gray-900/60 border border-cardborder p-4 rounded-xl flex items-center justify-between">
              <div>
                <p class="text-xs font-semibold text-white">Force Full Rescan</p>
                <p class="text-xs text-gray-400 mt-0.5">Scans all files for updates</p>
              </div>
              <button onclick="triggerRescan('')" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white">Rescan All</button>
            </div>

            <div class="bg-gray-900/60 border border-cardborder p-4 rounded-xl flex items-center justify-between">
              <div>
                <p class="text-xs font-semibold text-white">Proton VPN NAT-PMP Lease</p>
                <p class="text-xs text-gray-400 mt-0.5" id="vpn-status-desc">Port: 57417</p>
              </div>
              <button onclick="refreshVpnLease()" class="px-3 py-1.5 rounded-lg text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white">Refresh Lease</button>
            </div>
          </div>
        </div>

      </div>

    </div>

  </main>

  <footer class="border-t border-cardborder py-4 text-center text-xs text-gray-500">
    Syncthing Control Hub &bull; Laptop Host: <span class="font-mono text-gray-400">192.168.50.55:8386</span> &bull; 60-Day Retention Active
  </footer>

  <script>
    let dailyChart = null;
    let donutChart = null;
    let currentFolderId = "";
    let currentBrowseSubpath = "";

    // Toast Notification
    function showToast(msg, isSuccess = true) {
      const toast = document.getElementById('toast');
      const icon = document.getElementById('toast-icon');
      const text = document.getElementById('toast-msg');
      text.innerText = msg;
      icon.innerHTML = isSuccess
        ? '<div class="p-1 rounded-full bg-emerald-500/20 text-emerald-400"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M5 13l4 4L19 7"></path></svg></div>'
        : '<div class="p-1 rounded-full bg-rose-500/20 text-rose-400"><svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M6 18L18 6M6 6l12 12"></path></svg></div>';
      toast.classList.remove('translate-y-24', 'opacity-0');
      toast.classList.add('translate-y-0', 'opacity-100');
      setTimeout(() => {
        toast.classList.add('translate-y-24', 'opacity-0');
        toast.classList.remove('translate-y-0', 'opacity-100');
      }, 4000);
    }

    // Modal Control
    function closePreviewModal() {
      document.getElementById('preview-modal').classList.add('hidden');
      document.getElementById('preview-body').innerHTML = '';
    }

    function closeSettingsModal() {
      document.getElementById('settings-modal').classList.add('hidden');
    }

    // Open on Laptop Helper
    async function openLocalPath(path) {
      try {
        const res = await fetch('/api/open-local', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: path })
        });
        const data = await res.json();
        if (data.success) {
          showToast(data.message, true);
        } else {
          showToast(data.error || "Failed to open on laptop", false);
        }
      } catch (e) {
        showToast("Error communicating with server", false);
      }
    }

    function openCurrentFolderOnLaptop() {
      fetch(`/api/open-local`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: currentFolderId })
      }).then(r => r.json()).then(d => {
        showToast(d.message || (d.success ? "Opened folder on laptop" : "Error opening"), d.success);
      });
    }

    function openCurrentExplorerDirOnLaptop() {
      fetch(`/api/open-local`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: currentFolderId, subpath: currentBrowseSubpath })
      }).then(r => r.json()).then(d => {
        showToast(d.message || (d.success ? "Opened directory on laptop" : "Error opening"), d.success);
      });
    }

    // Switch Tabs
    function switchTab(tab) {
      ['explorer', 'retention', 'synclog', 'folders', 'control'].forEach(t => {
        const pane = document.getElementById(`tab-pane-${t}`);
        const btn = document.getElementById(`tab-btn-${t}`);
        if (t === tab) {
          pane.classList.remove('hidden');
          btn.classList.add('border-brand-500', 'text-brand-400');
          btn.classList.remove('border-transparent', 'text-gray-400');
        } else {
          pane.classList.add('hidden');
          btn.classList.remove('border-brand-500', 'text-brand-400');
          btn.classList.add('border-transparent', 'text-gray-400');
        }
      });
      if (tab === 'explorer') browseToSubpath(currentBrowseSubpath);
      if (tab === 'retention') loadRetentionFiles();
      if (tab === 'synclog') loadSyncLog();
      if (tab === 'folders') loadFolderBreakdown();
      if (tab === 'control') loadControlCenter();
    }

    function onFolderChange(newFid) {
      currentFolderId = newFid;
      currentBrowseSubpath = "";
      refreshAll();
    }

    // --- Overview & Header ---
    async function loadOverview() {
      try {
        const res = await fetch(`/api/overview?folder_id=${encodeURIComponent(currentFolderId)}`);
        const data = await res.json();
        if (!data) return;

        // Populate global folder select
        const sel = document.getElementById('global-folder-select');
        const currVal = sel.value;
        sel.innerHTML = data.all_folders.map(f => `<option value="${f.id}">${f.label} (${f.id})</option>`).join('');
        if (!currentFolderId && data.current_folder.id) {
          currentFolderId = data.current_folder.id;
        }
        sel.value = currentFolderId;

        // KPI Updates (Folders in GB, Files in MB)
        document.getElementById('kpi-total-storage').innerText = data.current_folder.total_gb;
        document.getElementById('kpi-total-files').innerText = `${Number(data.current_folder.total_files).toLocaleString()} files in folder`;
        document.getElementById('kpi-sync-state').innerText = data.current_folder.state;
        document.getElementById('kpi-sync-bar').style.width = `${data.current_folder.sync_percentage}%`;

        document.getElementById('kpi-retention-count').innerText = `${data.retention.file_count} files`;
        document.getElementById('kpi-retention-size').innerText = `${data.retention.total_gb} preserved on laptop`;
        document.getElementById('tab-retention-badge').innerText = data.retention.file_count;

        document.getElementById('kpi-today-volume').innerText = data.today.bytes_mb;
        document.getElementById('kpi-today-files').innerText = `${data.today.files_added} files added/updated`;
        document.getElementById('kpi-today-deleted').innerText = data.today.files_deleted;

        document.getElementById('kpi-device-name').innerText = data.device.name;
        document.getElementById('kpi-device-ip').innerText = data.device.address;
        document.getElementById('kpi-vpn-port').innerText = data.vpn.port || "57417";
        document.getElementById('vpn-status-desc').innerText = `Exit: ${data.vpn.ip} | Port: ${data.vpn.port}`;

        // Connection Pill
        const pill = document.getElementById('status-pill');
        if (data.device.connected) {
          pill.className = "flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs font-semibold badge-safe";
          pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span><span>Connected: ${data.device.connection_mode}</span>`;
        } else {
          pill.className = "flex items-center space-x-2 px-3 py-1.5 rounded-lg text-xs font-semibold bg-gray-800 text-gray-400 border border-gray-700";
          pill.innerHTML = `<span class="w-2 h-2 rounded-full bg-gray-500"></span><span>Device Disconnected</span>`;
        }
      } catch (e) {
        console.error("Overview error:", e);
      }
    }

    // --- Interactive File Explorer ---
    async function browseToSubpath(subpath = "") {
      currentBrowseSubpath = subpath;
      try {
        const res = await fetch(`/api/browse?folder_id=${encodeURIComponent(currentFolderId)}&subpath=${encodeURIComponent(subpath)}`);
        const data = await res.json();
        if (!data || !data.items) return;

        // Render Breadcrumbs
        const bc = document.getElementById('explorer-breadcrumbs');
        bc.innerHTML = data.breadcrumbs.map((b, idx) => {
          const isLast = idx === data.breadcrumbs.length - 1;
          if (isLast) {
            return `<span class="font-bold text-white">${b.name}</span>`;
          } else {
            return `<button onclick="browseToSubpath('${b.subpath.replace(/'/g, "\\'")}')" class="text-indigo-400 hover:text-indigo-300 font-semibold underline">${b.name}</button><span class="text-gray-600">/</span>`;
          }
        }).join('');

        // Render Table Rows
        const tbody = document.getElementById('explorer-tbody');
        let html = '';

        if (data.parent_subpath !== null) {
          html += `
            <tr class="hover:bg-gray-800/40 transition cursor-pointer" onclick="browseToSubpath('${data.parent_subpath.replace(/'/g, "\\'")}')">
              <td colspan="5" class="py-2.5 px-4 font-semibold text-indigo-400 flex items-center gap-2">
                <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M11 15l-3-3m0 0l3-3m-3 3h8M3 12a9 9 0 1118 0 9 9 0 0118 0z"></path></svg>
                .. Up to Parent Directory
              </td>
            </tr>
          `;
        }

        if (data.items.length === 0) {
          html += `<tr><td colspan="5" class="text-center py-8 text-gray-500">This directory is empty.</td></tr>`;
        } else {
          html += data.items.map(item => {
            const iconSvg = item.is_dir
              ? `<svg class="w-5 h-5 text-amber-400" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>`
              : getFileIconSvg(item.media_type);

            const sizeDisplay = item.is_dir ? item.size_gb : item.size_mb;

            return `
              <tr class="hover:bg-gray-800/40 transition">
                <td class="py-3 px-4 font-medium text-white flex items-center gap-2.5">
                  ${iconSvg}
                  ${item.is_dir 
                    ? `<button onclick="browseToSubpath('${item.subpath.replace(/'/g, "\\'")}')" class="font-bold text-gray-200 hover:text-brand-400 transition text-left underline">${item.name}</button>`
                    : `<span class="text-gray-200">${item.name}</span>`
                  }
                </td>
                <td class="py-3 px-4 text-xs text-gray-400">${item.is_dir ? 'Directory' : item.ext.toUpperCase().replace('.', '') + ' File'}</td>
                <td class="py-3 px-4 font-mono text-xs text-indigo-300 font-semibold">${sizeDisplay}</td>
                <td class="py-3 px-4 font-mono text-xs text-gray-400">${item.mtime}</td>
                <td class="py-3 px-4 text-right space-x-2">
                  <button onclick="openLocalPath('${item.absolute_path.replace(/'/g, "\\'")}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-indigo-300 border border-gray-700 transition" title="Open and highlight in Nautilus file manager on laptop">
                    📂 Reveal on Laptop
                  </button>
                  ${!item.is_dir ? `
                    <button onclick="previewFile('${item.subpath.replace(/'/g, "\\'")}', '${item.name.replace(/'/g, "\\'")}', '${item.media_type}', '${item.absolute_path.replace(/'/g, "\\'")}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white transition">
                      👁️ Preview
                    </button>
                  ` : ''}
                </td>
              </tr>
            `;
          }).join('');
        }

        tbody.innerHTML = html;
      } catch (e) {
        console.error("Browse error:", e);
      }
    }

    function getFileIconSvg(type) {
      if (type === 'image') return `<svg class="w-5 h-5 text-emerald-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z"></path></svg>`;
      if (type === 'video') return `<svg class="w-5 h-5 text-rose-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15 10l4.553-2.276A1 1 0 0121 8.618v6.764a1 1 0 01-1.447.894L15 14M5 18h8a2 2 0 002-2V8a2 2 0 00-2-2H5a2 2 0 00-2 2v8a2 2 0 002 2z"></path></svg>`;
      if (type === 'audio') return `<svg class="w-5 h-5 text-purple-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 19V6l12-3v13M9 19c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zm12-3c0 1.105-1.343 2-3 2s-3-.895-3-2 1.343-2 3-2 3 .895 3 2zM9 10l12-3"></path></svg>`;
      if (type === 'pdf') return `<svg class="w-5 h-5 text-red-500" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M7 21h10a2 2 0 002-2V9.414a1 1 0 00-.293-.707l-5.414-5.414A1 1 0 0012.586 3H7a2 2 0 00-2 2v14a2 2 0 002 2z"></path></svg>`;
      return `<svg class="w-5 h-5 text-blue-400" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>`;
    }

    function previewFile(subpath, name, type, absPath) {
      const url = `/api/file-content?folder_id=${encodeURIComponent(currentFolderId)}&path=${encodeURIComponent(subpath)}`;
      document.getElementById('preview-title').innerText = name;
      document.getElementById('preview-download-link').href = url;
      document.getElementById('preview-open-laptop-btn').onclick = () => openLocalPath(absPath);

      const body = document.getElementById('preview-body');
      if (type === 'image') {
        body.innerHTML = `<img src="${url}" class="max-h-[70vh] max-w-full rounded-lg object-contain shadow-xl" />`;
      } else if (type === 'video') {
        body.innerHTML = `<video controls class="max-h-[70vh] max-w-full rounded-lg shadow-xl" src="${url}"></video>`;
      } else if (type === 'audio') {
        body.innerHTML = `<audio controls class="w-full max-w-md" src="${url}"></audio>`;
      } else {
        body.innerHTML = `<iframe src="${url}" class="w-full h-[65vh] rounded-lg border border-gray-800 bg-white"></iframe>`;
      }
      document.getElementById('preview-modal').classList.remove('hidden');
    }

    // --- Charts ---
    async function loadDailyChart() {
      try {
        const res = await fetch('/api/daily-activity');
        const data = await res.json();
        if (!data || !data.activity) return;

        const labels = data.activity.map(a => a.date.slice(5));
        const volumes = data.activity.map(a => a.size_mb);
        const added = data.activity.map(a => a.files_added);
        const deleted = data.activity.map(a => a.files_deleted);

        const ctx = document.getElementById('dailySyncChart').getContext('2d');
        if (dailyChart) dailyChart.destroy();

        dailyChart = new Chart(ctx, {
          type: 'bar',
          data: {
            labels: labels,
            datasets: [
              {
                label: 'Volume (MB)',
                data: volumes,
                backgroundColor: 'rgba(99, 102, 241, 0.7)',
                borderColor: '#6366f1',
                borderWidth: 1,
                borderRadius: 4,
                yAxisID: 'y'
              },
              {
                label: 'Added',
                data: added,
                type: 'line',
                borderColor: '#10b981',
                backgroundColor: '#10b981',
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 3,
                yAxisID: 'y1'
              },
              {
                label: 'Deleted',
                data: deleted,
                type: 'line',
                borderColor: '#f43f5e',
                backgroundColor: '#f43f5e',
                tension: 0.3,
                borderWidth: 2,
                pointRadius: 3,
                yAxisID: 'y1'
              }
            ]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            scales: {
              x: { grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af' } },
              y: { type: 'linear', position: 'left', grid: { color: 'rgba(255,255,255,0.05)' }, ticks: { color: '#9ca3af', callback: v => v + ' MB' } },
              y1: { type: 'linear', position: 'right', grid: { drawOnChartArea: false }, ticks: { color: '#9ca3af', precision: 0 } }
            },
            plugins: { legend: { labels: { color: '#d1d5db', boxWidth: 12 } } }
          }
        });
      } catch (e) {
        console.error("Daily chart error:", e);
      }
    }

    async function loadFolderBreakdown() {
      try {
        const res = await fetch(`/api/folders?folder_id=${encodeURIComponent(currentFolderId)}`);
        const data = await res.json();
        if (!data || !data.folders) return;

        // Populate Table (Sizes in GB)
        const tbody = document.getElementById('folders-tbody');
        tbody.innerHTML = data.folders.map(f => `
          <tr class="hover:bg-gray-800/40 transition">
            <td class="py-3 px-4 font-semibold text-white flex items-center gap-2">
              <svg class="w-4 h-4 text-indigo-400" fill="currentColor" viewBox="0 0 20 20"><path d="M2 6a2 2 0 012-2h5l2 2h5a2 2 0 012 2v6a2 2 0 01-2 2H4a2 2 0 01-2-2V6z"></path></svg>
              <button onclick="browseToSubpath('${f.path.replace(/'/g, "\\'")}')" class="hover:text-brand-400 underline">${f.name}</button>
            </td>
            <td class="py-3 px-4 text-gray-300 font-mono">${Number(f.files).toLocaleString()}</td>
            <td class="py-3 px-4 text-indigo-300 font-mono font-bold">${f.size_gb}</td>
            <td class="py-3 px-4">
              <div class="flex items-center gap-3">
                <div class="flex-1 bg-gray-800 rounded-full h-1.5 max-w-[120px]">
                  <div class="bg-indigo-500 h-1.5 rounded-full" style="width: ${f.percentage}%"></div>
                </div>
                <span class="text-xs text-gray-400 font-mono">${f.percentage}%</span>
              </div>
            </td>
            <td class="py-3 px-4 text-right">
              <button onclick="openLocalPath('${f.absolute_path.replace(/'/g, "\\'")}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-indigo-300 border border-gray-700 transition">
                📂 Open on Laptop
              </button>
            </td>
          </tr>
        `).join('');

        // Populate Donut Chart in GB
        const top5 = data.folders.slice(0, 5);
        const otherSize = data.folders.slice(5).reduce((sum, f) => sum + f.size_bytes, 0);
        const donutLabels = top5.map(f => f.name);
        const donutData = top5.map(f => (f.size_bytes / (1024*1024*1024)).toFixed(2));
        if (otherSize > 0) {
          donutLabels.push('Other');
          donutData.push((otherSize / (1024*1024*1024)).toFixed(2));
        }

        const ctx = document.getElementById('folderDonutChart').getContext('2d');
        if (donutChart) donutChart.destroy();

        donutChart = new Chart(ctx, {
          type: 'doughnut',
          data: {
            labels: donutLabels,
            datasets: [{
              data: donutData,
              backgroundColor: ['#6366f1', '#10b981', '#f59e0b', '#3b82f6', '#ec4899', '#6b7280'],
              borderWidth: 2,
              borderColor: '#101625'
            }]
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'bottom', labels: { color: '#d1d5db', boxWidth: 10, font: { size: 11 } } },
              tooltip: { callbacks: { label: item => ` ${item.label}: ${item.raw} GB` } }
            },
            cutout: '70%'
          }
        });
      } catch (e) {
        console.error("Folder breakdown error:", e);
      }
    }

    // --- 60-Day Retention Vault ---
    async function loadRetentionFiles() {
      try {
        const folder = document.getElementById('retention-folder-filter').value;
        const search = document.getElementById('retention-search').value;
        const sort = document.getElementById('retention-sort').value;

        let url = `/api/retention-files?sort=${sort}`;
        if (folder && folder !== 'all') url += `&folder=${encodeURIComponent(folder)}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;

        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.getElementById('retention-tbody');

        if (!data || !data.files || data.files.length === 0) {
          tbody.innerHTML = `<tr><td colspan="6" class="text-center py-10 text-gray-500">No deleted files in retention holding.</td></tr>`;
          return;
        }

        tbody.innerHTML = data.files.map(f => {
          let badgeClass = "badge-safe";
          if (f.days_remaining <= 10) badgeClass = "badge-danger";
          else if (f.days_remaining <= 30) badgeClass = "badge-warning";

          return `
            <tr class="hover:bg-gray-800/40 transition">
              <td class="py-3 px-4 font-mono text-xs text-white max-w-xs truncate" title="${f.relative_path}">
                <div class="font-bold text-sm text-gray-200">${f.file_name}</div>
                <div class="text-gray-500 truncate">${f.relative_path}</div>
              </td>
              <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-xs bg-gray-800 text-gray-300 border border-gray-700">${f.root_folder}</span></td>
              <td class="py-3 px-4 font-mono text-xs text-indigo-300 font-bold">${f.size_mb}</td>
              <td class="py-3 px-4 font-mono text-xs text-gray-400">${f.deleted_timestamp}</td>
              <td class="py-3 px-4"><span class="px-2.5 py-1 rounded-full text-xs font-bold ${badgeClass}">${f.days_remaining} days left</span></td>
              <td class="py-3 px-4 text-right space-x-2">
                <button onclick="openLocalPath('${f.absolute_path.replace(/'/g, "\\'")}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-indigo-300 border border-gray-700 transition">
                  📂 Show on Laptop
                </button>
                <button onclick="restoreFile('${f.relative_path.replace(/'/g, "\\'")}', '${f.folder_id}')" class="px-3 py-1 rounded text-xs font-bold bg-emerald-600 hover:bg-emerald-500 text-white transition">
                  Restore
                </button>
              </td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        console.error("Retention error:", e);
      }
    }

    async function restoreFile(path, folderId) {
      if (!confirm(`Restore "${path}" back to active storage?`)) return;
      try {
        const res = await fetch('/api/restore', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ path: path, folder_id: folderId })
        });
        const data = await res.json();
        showToast(data.message || (data.success ? "File restored" : "Restore failed"), data.success);
        loadRetentionFiles();
        loadOverview();
      } catch (e) {
        showToast("Error connecting to server", false);
      }
    }

    // --- Live Sync Log ---
    async function loadSyncLog() {
      try {
        const action = document.getElementById('synclog-action-filter').value;
        const folder = document.getElementById('synclog-folder-filter').value;
        const search = document.getElementById('synclog-search').value;

        let url = `/api/sync-log?limit=50`;
        if (action && action !== 'all') url += `&action=${action}`;
        if (folder && folder !== 'all') url += `&folder=${encodeURIComponent(folder)}`;
        if (search) url += `&search=${encodeURIComponent(search)}`;

        const res = await fetch(url);
        const data = await res.json();
        const tbody = document.getElementById('synclog-tbody');

        if (!data || !data.items || data.items.length === 0) {
          tbody.innerHTML = `<tr><td colspan="6" class="text-center py-8 text-gray-500">No sync events found.</td></tr>`;
          return;
        }

        tbody.innerHTML = data.items.map(item => {
          const badge = item.action === 'delete'
            ? `<span class="px-2 py-0.5 rounded text-xs font-semibold badge-danger">Deleted on Phone</span>`
            : `<span class="px-2 py-0.5 rounded text-xs font-semibold badge-safe">Added / Synced</span>`;

          return `
            <tr class="hover:bg-gray-800/40 transition">
              <td class="py-3 px-4 font-mono text-xs text-gray-400 whitespace-nowrap">${item.timestamp}</td>
              <td class="py-3 px-4">${badge}</td>
              <td class="py-3 px-4 font-mono text-xs text-white max-w-sm truncate" title="${item.item_path}">${item.item_path}</td>
              <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-xs bg-gray-800 text-gray-300 border border-gray-700">${item.root_folder}</span></td>
              <td class="py-3 px-4 font-mono text-xs text-indigo-300 font-bold">${item.size_mb}</td>
              <td class="py-3 px-4 text-right">
                <button onclick="openLocalPath('${item.item_path.replace(/'/g, "\\'")}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-indigo-300 border border-gray-700 transition">
                  📂 Reveal
                </button>
              </td>
            </tr>
          `;
        }).join('');
      } catch (e) {
        console.error("Sync log error:", e);
      }
    }

    // --- Full Settings & Control Center ---
    async function loadControlCenter() {
      try {
        const res = await fetch('/api/config/full');
        const data = await res.json();
        if (!data) return;

        document.getElementById('control-folders-count').innerText = data.folders.length;
        document.getElementById('control-devices-count').innerText = data.devices.length;

        // Folders Table
        const fTbody = document.getElementById('control-folders-tbody');
        fTbody.innerHTML = data.folders.map(f => `
          <tr class="hover:bg-gray-800/40 transition">
            <td class="py-3 px-4">
              <div class="font-bold text-white text-sm">${f.label}</div>
              <div class="text-xs font-mono text-gray-500">${f.id}</div>
            </td>
            <td class="py-3 px-4 font-mono text-xs text-gray-300 max-w-xs truncate" title="${f.path}">${f.path}</td>
            <td class="py-3 px-4"><span class="px-2 py-0.5 rounded text-xs bg-gray-800 text-indigo-300 border border-gray-700">${f.type}</span></td>
            <td class="py-3 px-4 font-mono text-xs text-indigo-300 font-bold">${f.size_gb}</td>
            <td class="py-3 px-4">
              <span class="px-2 py-0.5 rounded-full text-xs font-bold ${f.paused ? 'badge-warning' : 'badge-safe'}">
                ${f.paused ? 'Paused' : 'Active'}
              </span>
            </td>
            <td class="py-3 px-4 text-right space-x-1.5">
              <button onclick="toggleFolderPause('${f.id}', ${!f.paused})" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700">
                ${f.paused ? '▶ Resume' : '⏸ Pause'}
              </button>
              <button onclick="triggerRescan('${f.id}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-indigo-600 hover:bg-indigo-500 text-white">
                🔄 Rescan
              </button>
              <button onclick="openStignoreModal('${f.id}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-emerald-400 border border-gray-700">
                📝 .stignore
              </button>
              <button onclick="openLocalPath('${f.path.replace(/'/g, "\\'")}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-gray-300 border border-gray-700">
                📂 Open
              </button>
            </td>
          </tr>
        `).join('');

        // Devices Table
        const dTbody = document.getElementById('control-devices-tbody');
        dTbody.innerHTML = data.devices.map(d => `
          <tr class="hover:bg-gray-800/40 transition">
            <td class="py-3 px-4">
              <div class="font-bold text-white text-sm">${d.name}</div>
              <div class="text-xs font-mono text-gray-500">${d.deviceID.slice(0, 15)}...</div>
            </td>
            <td class="py-3 px-4 font-mono text-xs text-gray-300">${d.address || 'dynamic'}</td>
            <td class="py-3 px-4 font-mono text-xs text-gray-400">↓ ${d.in_formatted} &bull; ↑ ${d.out_formatted}</td>
            <td class="py-3 px-4">
              <span class="px-2 py-0.5 rounded-full text-xs font-bold ${d.is_connected ? 'badge-safe' : 'badge-warning'}">
                ${d.is_connected ? 'Connected' : (d.paused ? 'Paused' : 'Offline')}
              </span>
            </td>
            <td class="py-3 px-4 text-right space-x-1.5">
              <button onclick="toggleDevicePause('${d.deviceID}', ${!d.paused})" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700">
                ${d.paused ? '▶ Resume' : '⏸ Pause'}
              </button>
              <button onclick="openEditDeviceModal('${d.deviceID}', '${d.name.replace(/'/g, "\\'")}', '${(d.addresses||[]).join(', ')}')" class="px-2.5 py-1 rounded text-xs font-semibold bg-gray-800 hover:bg-gray-700 text-indigo-300 border border-gray-700">
                ⚙️ Edit
              </button>
            </td>
          </tr>
        `).join('');
      } catch (e) {
        console.error("Control center error:", e);
      }
    }

    // --- Folder / Device Operations ---
    function triggerRescan(fid) {
      fetch('/api/config/folder/rescan', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: fid })
      }).then(r => r.json()).then(d => {
        showToast(d.message || "Rescan triggered", d.success);
      });
    }

    function toggleFolderPause(fid, pauseState) {
      fetch('/api/config/folder/pause', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: fid, paused: pauseState })
      }).then(r => r.json()).then(d => {
        showToast(d.message || "Updated", d.success);
        loadControlCenter();
        loadOverview();
      });
    }

    function toggleDevicePause(did, pauseState) {
      fetch('/api/config/device/pause', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deviceID: did, paused: pauseState })
      }).then(r => r.json()).then(d => {
        showToast(d.message || "Updated", d.success);
        loadControlCenter();
        loadOverview();
      });
    }

    function restartSyncthing() {
      if (!confirm("Are you sure you want to restart the Syncthing engine?")) return;
      fetch('/api/system/restart', { method: 'POST' }).then(r => r.json()).then(d => {
        showToast("Syncthing restart command dispatched", true);
      });
    }

    function refreshVpnLease() {
      fetch('/api/vpn/refresh', { method: 'POST' }).then(r => r.json()).then(d => {
        showToast(d.message || "VPN Port Lease Refreshed", d.success);
        loadOverview();
      });
    }

    // --- Modals (Add Folder, Add Device, Edit .stignore) ---
    function openStignoreModal(fid) {
      fetch(`/api/config/folder/stignore?folder_id=${encodeURIComponent(fid)}`)
        .then(r => r.json())
        .then(data => {
          document.getElementById('settings-modal-title').innerText = `Edit .stignore rules (${fid})`;
          document.getElementById('settings-modal-body').innerHTML = `
            <p class="text-xs text-gray-400">One ignore pattern per line. Use <code class="text-indigo-400">(?d)</code> prefix to delete ignored items safely.</p>
            <textarea id="stignore-editor" rows="12" class="w-full bg-gray-900 border border-cardborder rounded-xl p-3 font-mono text-xs text-emerald-400 focus:outline-none focus:border-brand-500">${data.content || ''}</textarea>
          `;
          document.getElementById('settings-modal-footer').innerHTML = `
            <button onclick="closeSettingsModal()" class="px-4 py-2 rounded-xl text-xs font-semibold bg-gray-800 text-gray-300">Cancel</button>
            <button onclick="saveStignore('${fid}')" class="px-4 py-2 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white">Save Rules</button>
          `;
          document.getElementById('settings-modal').classList.remove('hidden');
        });
    }

    function saveStignore(fid) {
      const content = document.getElementById('stignore-editor').value;
      fetch('/api/config/folder/stignore', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ folder_id: fid, content: content })
      }).then(r => r.json()).then(d => {
        showToast(d.message || "Saved .stignore", d.success);
        closeSettingsModal();
      });
    }

    function openAddFolderModal() {
      document.getElementById('settings-modal-title').innerText = "Add New Synced Folder";
      document.getElementById('settings-modal-body').innerHTML = `
        <div class="space-y-3 text-xs">
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Folder ID (Unique identifier)</label>
            <input type="text" id="new-folder-id" placeholder="e.g. phone-2-storage" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 text-white">
          </div>
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Folder Label (Display Name)</label>
            <input type="text" id="new-folder-label" placeholder="e.g. Phone 2 Storage" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 text-white">
          </div>
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Local Laptop Storage Path</label>
            <input type="text" id="new-folder-path" value="/media/spicykiller/New Volume/Syncthing/Phone 2" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 text-white">
          </div>
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Folder Type</label>
            <select id="new-folder-type" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 text-white">
              <option value="sendreceive">Send & Receive (Bidirectional)</option>
              <option value="sendonly">Send Only</option>
              <option value="receiveonly">Receive Only</option>
            </select>
          </div>
          <div>
            <label class="block font-semibold text-gray-300 mb-1">60-Day Trashcan Retention</label>
            <input type="number" id="new-folder-cleanout" value="60" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 text-white">
          </div>
        </div>
      `;
      document.getElementById('settings-modal-footer').innerHTML = `
        <button onclick="closeSettingsModal()" class="px-4 py-2 rounded-xl text-xs font-semibold bg-gray-800 text-gray-300">Cancel</button>
        <button onclick="submitAddFolder()" class="px-4 py-2 rounded-xl text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white">Create Folder</button>
      `;
      document.getElementById('settings-modal').classList.remove('hidden');
    }

    function submitAddFolder() {
      const fid = document.getElementById('new-folder-id').value.trim();
      const label = document.getElementById('new-folder-label').value.trim();
      const path = document.getElementById('new-folder-path').value.trim();
      const type = document.getElementById('new-folder-type').value;
      const cleanout = parseInt(document.getElementById('new-folder-cleanout').value || '60');

      fetch('/api/config/folder/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: fid, label: label, path: path, type: type, cleanout_days: cleanout })
      }).then(r => r.json()).then(d => {
        showToast(d.message || (d.success ? "Folder created" : "Error creating"), d.success);
        closeSettingsModal();
        loadControlCenter();
        loadOverview();
      });
    }

    function openAddDeviceModal() {
      document.getElementById('settings-modal-title').innerText = "Pair New Device";
      document.getElementById('settings-modal-body').innerHTML = `
        <div class="space-y-3 text-xs">
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Device ID (From phone's Show Device ID screen)</label>
            <input type="text" id="new-device-id" placeholder="e.g. ABCDEFG-1234567-..." class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 font-mono text-white">
          </div>
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Device Name</label>
            <input type="text" id="new-device-name" placeholder="e.g. Phone 2 (OnePlus)" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 text-white">
          </div>
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Addresses (Recommended: dynamic)</label>
            <input type="text" id="new-device-address" value="dynamic" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 font-mono text-white">
          </div>
        </div>
      `;
      document.getElementById('settings-modal-footer').innerHTML = `
        <button onclick="closeSettingsModal()" class="px-4 py-2 rounded-xl text-xs font-semibold bg-gray-800 text-gray-300">Cancel</button>
        <button onclick="submitAddDevice()" class="px-4 py-2 rounded-xl text-xs font-semibold bg-emerald-600 hover:bg-emerald-500 text-white">Pair Device</button>
      `;
      document.getElementById('settings-modal').classList.remove('hidden');
    }

    function submitAddDevice() {
      const did = document.getElementById('new-device-id').value.trim();
      const name = document.getElementById('new-device-name').value.trim();
      const addrs = document.getElementById('new-device-address').value.trim();

      fetch('/api/config/device/add', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deviceID: did, name: name, addresses: [addrs] })
      }).then(r => r.json()).then(d => {
        showToast(d.message || (d.success ? "Device paired" : "Error pairing"), d.success);
        closeSettingsModal();
        loadControlCenter();
        loadOverview();
      });
    }

    function openEditDeviceModal(did, name, addrs) {
      document.getElementById('settings-modal-title').innerText = `Edit Device: ${name}`;
      document.getElementById('settings-modal-body').innerHTML = `
        <div class="space-y-3 text-xs">
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Device Name</label>
            <input type="text" id="edit-device-name" value="${name}" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 text-white">
          </div>
          <div>
            <label class="block font-semibold text-gray-300 mb-1">Addresses (comma separated)</label>
            <input type="text" id="edit-device-addresses" value="${addrs}" class="w-full bg-gray-900 border border-cardborder rounded-lg p-2.5 font-mono text-white">
          </div>
        </div>
      `;
      document.getElementById('settings-modal-footer').innerHTML = `
        <button onclick="closeSettingsModal()" class="px-4 py-2 rounded-xl text-xs font-semibold bg-gray-800 text-gray-300">Cancel</button>
        <button onclick="submitEditDevice('${did}')" class="px-4 py-2 rounded-xl text-xs font-semibold bg-brand-600 hover:bg-brand-500 text-white">Save Changes</button>
      `;
      document.getElementById('settings-modal').classList.remove('hidden');
    }

    function submitEditDevice(did) {
      const name = document.getElementById('edit-device-name').value.trim();
      const addrs = document.getElementById('edit-device-addresses').value.trim();
      fetch('/api/config/device/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ deviceID: did, name: name, addresses: addrs })
      }).then(r => r.json()).then(d => {
        showToast(d.message || "Updated device", d.success);
        closeSettingsModal();
        loadControlCenter();
      });
    }

    function refreshAll() {
      loadOverview();
      browseToSubpath(currentBrowseSubpath);
      loadDailyChart();
      loadFolderBreakdown();
      loadRetentionFiles();
      loadSyncLog();
      loadControlCenter();
    }

    // Auto Refresh
    document.addEventListener('DOMContentLoaded', () => {
      refreshAll();
      setInterval(loadOverview, 5000);
      setInterval(() => {
        loadDailyChart();
        loadRetentionFiles();
      }, 30000);
    });
  </script>
</body>
</html>
"""

def main():
    print(f"Starting Syncthing Advanced Hub on port {PORT}...")
    
    # Pre-scan configured folders
    for f in get_all_folders_config():
        fid = f.get('id')
        fpath = f.get('path')
        if fid and fpath:
            scan_single_folder(fid, fpath)
            
    scan_all_retention_vaults()

    t_events = threading.Thread(target=event_collector_worker, daemon=True)
    t_events.start()

    t_scanner = threading.Thread(target=periodic_scanner_worker, daemon=True)
    t_scanner.start()

    server_address = ('0.0.0.0', PORT)
    httpd = ThreadedHTTPServer(server_address, DashboardHandler)
    print(f"Listening at http://0.0.0.0:{PORT} (LAN: http://192.168.50.55:{PORT})")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        httpd.server_close()

if __name__ == "__main__":
    main()
