import customtkinter as ctk
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import yt_dlp
from yt_dlp.utils import download_range_func
import threading
import concurrent.futures
import os
import sys
import re
import sqlite3
import shutil
import tempfile
import datetime
import subprocess
import psutil
import time
import socket
from PIL import Image, ImageDraw


try:
    from flask import Flask, request, jsonify, render_template_string
    import qrcode
    SERVER_AVAILABLE = True
except ImportError:
    SERVER_AVAILABLE = False

try:
    import darkdetect
except ImportError:
    darkdetect = None

try:
    import pystray
    TRAY_AVAILABLE = True
except ImportError:
    TRAY_AVAILABLE = False

try:
    from winotify import Notification
    NOTIFICATIONS_AVAILABLE = True
except ImportError:
    NOTIFICATIONS_AVAILABLE = False

# --- Patch shutil.copy2 to handle locked files (Chrome cookies) ---
# Chrome locks its SQLite cookie DB while running. yt-dlp internally uses
# shutil.copy2 to copy it, which fails. This patch falls back to reading
# the raw bytes when the standard copy fails due to a file lock.
_original_shutil_copy2 = shutil.copy2
def _safe_copy2(src, dst, *args, **kwargs):
    try:
        return _original_shutil_copy2(src, dst, *args, **kwargs)
    except (PermissionError, OSError):
        try:
            with open(src, 'rb') as f:
                data = f.read()
            dst_path = dst if not os.path.isdir(dst) else os.path.join(dst, os.path.basename(src))
            with open(dst_path, 'wb') as f:
                f.write(data)
            return dst_path
        except Exception:
            raise  # Re-raise if raw read also fails
shutil.copy2 = _safe_copy2


class StopDownloadException(Exception):
    """Custom exception to stop yt-dlp download gracefully."""
    pass

class YTDLLogger:
    """Custom logger that checks for cancellation at every message."""
    def __init__(self, app, item_id):
        self.app = app
        self.item_id = item_id

    def debug(self, msg): self._check_cancel()
    def info(self, msg): 
        # print(f"YT-DLP Info: {msg}") # Uncomment to see all info logs
        self._check_cancel()
        
    def warning(self, msg): self._check_cancel()
    
    def error(self, msg): 
        print(f"YT-DLP Error: {msg}")
        self._check_cancel()

    def _check_cancel(self):
        if self.app.stop_all_flag or self.app.active_downloads.get(self.item_id, False):
            print(f"YTDLLogger: Cancel detected for {self.item_id}")
            raise StopDownloadException()

class CTkContextMenu(ctk.CTkToplevel):
    def __init__(self, master, x, y, commands):
        super().__init__(master)
        self.overrideredirect(True)
        self.attributes("-topmost", True)
        self.configure(fg_color="#0D1117")
        self.geometry(f"+{x}+{y}")
        
        main_frame = ctk.CTkFrame(self, border_width=1, corner_radius=10,
                                  fg_color="#161B22", border_color="#30363D")
        main_frame.pack(fill=tk.BOTH, expand=True, padx=1, pady=1)

        for i, cmd_info in enumerate(commands):
            if cmd_info == "separator":
                ctk.CTkFrame(main_frame, height=1, fg_color="#30363D").pack(fill=tk.X, padx=12, pady=4)
                continue
            
            label, command = cmd_info
            btn = ctk.CTkButton(main_frame, text=label, command=lambda c=command: self.execute(c), 
                               font=("Segoe UI", 12), anchor="w", height=32, corner_radius=6,
                               fg_color="transparent", hover_color="#1C2333",
                               text_color="#E6EDF3")
            btn.pack(fill=tk.X, padx=5, pady=1)

        self.bind("<FocusOut>", lambda e: self.destroy())
        self.after(10, self.focus_force)

    def execute(self, cmd):
        self.destroy()
        cmd()

class CTkTooltip:
    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tooltip_window = None
        self.id = None
        self.widget.bind("<Enter>", self.schedule_show)
        self.widget.bind("<Leave>", self.hide)
        self.widget.bind("<ButtonPress>", self.hide)

    def schedule_show(self, event=None):
        self.id = self.widget.after(self.delay, self.show)

    def show(self):
        if self.tooltip_window or not self.text: return
        x, y, cx, cy = self.widget.bbox("insert")
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        
        self.tooltip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        tw.attributes("-topmost", True)
        
        frame = ctk.CTkFrame(tw, border_width=1, corner_radius=6,
                             fg_color="#161B22", border_color="#3B82F6")
        frame.pack()
        
        label = ctk.CTkLabel(frame, text=self.text, font=("Segoe UI", 11),
                             text_color="#E6EDF3", padx=10, pady=5)
        label.pack()

    def hide(self, event=None):
        if self.id:
            self.widget.after_cancel(self.id)
            self.id = None
        if self.tooltip_window:
            self.tooltip_window.destroy()
            self.tooltip_window = None

# --- HTML TEMPLATE FOR REMOTE ---
REMOTE_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>MDP Remote</title>
    <style>
        :root { --bg: #0F0F14; --panel: #1A1A24; --accent: #2196F3; --text: #FFFFFF; --text-dim: #A0A0A0; --danger: #F44336; --success: #4CAF50;}
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 15px; }
        .header { text-align: center; margin-bottom: 20px; }
        .header h2 { margin: 0; font-size: 22px; color: var(--accent); }
        .card { background: var(--panel); border-radius: 12px; padding: 15px; margin-bottom: 15px; box-shadow: 0 4px 6px rgba(0,0,0,0.3); }
        input[type="text"] { width: 100%; box-sizing: border-box; padding: 12px; border-radius: 8px; border: 1px solid #333; background: #000; color: #fff; margin-bottom: 10px; font-size: 16px; }
        button { background: var(--accent); color: white; border: none; padding: 12px; border-radius: 8px; font-size: 16px; font-weight: bold; width: 100%; cursor: pointer; transition: 0.2s; }
        button:active { opacity: 0.8; }
        .btn-group { display: flex; gap: 10px; margin-top: 10px; }
        .btn-danger { background: var(--danger); }
        .btn-success { background: var(--success); }
        .item { border-bottom: 1px solid #333; padding: 12px 0; }
        .item:last-child { border-bottom: none; }
        .item-title { font-weight: 500; font-size: 14px; margin-bottom: 5px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; display: block; }
        .item-meta { font-size: 12px; color: var(--text-dim); display: flex; justify-content: space-between; }
        .progress-bar { height: 4px; background: #333; border-radius: 2px; margin-top: 8px; overflow: hidden; }
        .progress-fill { height: 100%; background: var(--accent); width: 0%; transition: width 0.3s; }
        .status-badge { font-size: 10px; padding: 2px 6px; border-radius: 4px; background: #333; }
    </style>
</head>
<body>
    <div class="header">
        <h2>🚀 Media Downloader Pro</h2>
        <div id="global_status" style="font-size: 12px; color: var(--text-dim); margin-top: 5px;">Connecting...</div>
    </div>
    
    <div class="card">
        <input type="text" id="url_input" placeholder="Paste YouTube link here...">
        <button onclick="addLink()">🔗 Add Download</button>
    </div>
    
    <div class="card">
        <div style="font-size: 14px; font-weight: bold; margin-bottom: 10px;">Control Panel</div>
        <div class="btn-group">
            <button class="btn-success" onclick="sendCommand('resume_all')">⏵ Resume</button>
            <button style="background: #FF9800;" onclick="sendCommand('pause_all')">⏸ Pause</button>
            <button class="btn-danger" onclick="sendCommand('stop_all')">⏹ Stop</button>
        </div>
    </div>

    <div class="card" id="queue_container" style="display: none;">
        <div style="font-size: 14px; font-weight: bold; margin-bottom: 10px;">📁 Queue</div>
        <div id="items_list"></div>
    </div>

    <script>
        function sendCommand(cmd, item_id=null, url=null) {
            fetch('/api/action', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({command: cmd, item_id: item_id, url: url})
            });
        }
        
        function addLink() {
            const input = document.getElementById('url_input');
            const url = input.value.trim();
            if (url) {
                sendCommand('add_link', null, url);
                input.value = ''; // clear
            }
        }

        async function updateStatus() {
            try {
                const res = await fetch('/api/status');
                const data = await res.json();
                
                document.getElementById('global_status').innerText = data.global_status || 'Ready';
                
                const list = document.getElementById('items_list');
                const container = document.getElementById('queue_container');
                
                if (data.items.length > 0) {
                    container.style.display = 'block';
                    let html = '';
                    data.items.forEach(item => {
                        let isDone = item.status.includes('Done');
                        let isError = item.status.includes('Error');
                        let isDownloading = item.status.includes('Downloading');
                        let color = isDone ? 'var(--success)' : (isError ? 'var(--danger)' : 'var(--accent)');
                        
                        let actionBtn = '';
                        if (!isDone && !isError) {
                            if (isDownloading) {
                                actionBtn = `<button onclick="sendCommand('pause_item', '${item.id}')" style="width: auto; padding: 4px 10px; font-size: 10px; background: #FF9800; margin-left: 5px;">⏸</button>`;
                            } else {
                                actionBtn = `<button onclick="sendCommand('resume_item', '${item.id}')" style="width: auto; padding: 4px 10px; font-size: 10px; background: var(--success); margin-left: 5px;">⏵</button>`;
                            }
                        }

                        html += `
                            <div class="item">
                                <div class="item-title">${item.title || 'Fetching...'}</div>
                                <div class="item-meta">
                                    <span>${item.size || '---'}</span>
                                    <div style="display: flex; align-items: center;">
                                        <span class="status-badge" style="color: ${color}">${item.status}</span>
                                        ${actionBtn}
                                    </div>
                                </div>
                                <div class="progress-bar">
                                    <div class="progress-fill" style="width: ${item.progress}%; background: ${color};"></div>
                                </div>
                            </div>
                        `;
                    });
                    list.innerHTML = html;
                } else {
                    container.style.display = 'none';
                }
            } catch (e) {
                document.getElementById('global_status').innerText = 'Connection Lost';
            }
        }
        
        // Poll every 1.5 seconds
        setInterval(updateStatus, 1500);
        updateStatus(); // Initial call
    </script>
</body>
</html>
"""

class RemoteServer:
    def __init__(self, app_instance):
        self.app_instance = app_instance
        self.flask_app = Flask(__name__)
        self.setup_routes()
        
    def setup_routes(self):
        @self.flask_app.route('/')
        def index():
            return render_template_string(REMOTE_HTML)
            
        @self.flask_app.route('/api/status')
        def status():
            # Gather state from app_instance
            tree = self.app_instance.tree
            items = []
            if tree and self.app_instance.root.winfo_exists():
                for item_id in tree.get_children():
                    vals = tree.item(item_id, "values")
                    if vals:
                        try:
                            db_id = int(item_id.replace("db_", ""))
                            perc = 0
                            spd = ""
                            status_text = vals[7]
                            if "(" in status_text and "%" in status_text: # e.g. 45.2% (1.5MiB/s)
                                parts = status_text.split("(", 1)
                                try: perc = float(parts[0].replace("%", "").strip())
                                except: pass
                                spd = parts[1].replace(")", "").strip()
                                
                            items.append({
                                "id": db_id,
                                "tree_id": item_id,
                                "title": vals[2],
                                "size": vals[6],
                                "status": status_text,
                                "progress": perc,
                                "speed": spd
                            })
                        except Exception as e:
                            print(f"Error parsing item for remote: {e}")
                            pass
            
            return jsonify({
                "items": items,
                "global_progress": self.app_instance.progress_var.get(),
                "global_status": self.app_instance.status_label.cget("text")
            })
            
        @self.flask_app.route('/api/action', methods=['POST'])
        def action():
            data = request.json
            cmd = data.get('command')
            item_id = data.get('item_id') # Optional, for specific item
            
            def dispatch():
                if cmd == 'stop_all': self.app_instance.stop_all()
                elif cmd == 'pause_all':
                    # Select all downloading and pause
                    active = [i for i in self.app_instance.tree.get_children() if "Downloading" in self.app_instance.tree.item(i, "values")[7]]
                    self.app_instance.tree.selection_set(active)
                    self.app_instance.pause_selected()
                elif cmd == 'resume_all':
                    # Select queued/paused and resume
                    paused = [i for i in self.app_instance.tree.get_children() if self.app_instance.tree.item(i, "values")[7] in ["Paused", "Queued", "Error", "Stopped"]]
                    self.app_instance.tree.selection_set(paused)
                    self.app_instance.resume_selected()
                elif cmd == 'pause_item' and item_id:
                    tree_id = f"db_{item_id}"
                    if self.app_instance.tree.exists(tree_id):
                        self.app_instance.tree.selection_set((tree_id,))
                        self.app_instance.pause_selected()
                elif cmd == 'resume_item' and item_id:
                    tree_id = f"db_{item_id}"
                    if self.app_instance.tree.exists(tree_id):
                        self.app_instance.tree.selection_set((tree_id,))
                        self.app_instance.resume_selected()
                elif cmd == 'add_link':
                    url = data.get('url')
                    if url: self.app_instance.fetch_and_add(url, "Full Video")
            
            # Must run UI commands on main thread
            self.app_instance.root.after(0, dispatch)
            return jsonify({"status": "ok"})

    def run(self):
        # Run on 0.0.0.0 to allow LAN access
        import logging
        log = logging.getLogger('werkzeug')
        log.setLevel(logging.ERROR) # Quiet the output
        self.flask_app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)

# Default config

def get_ffmpeg_path():
    """Locates and returns the path to the ffmpeg executable or 'ffmpeg'."""
    if hasattr(sys, '_MEIPASS'):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Priority: 1. Application Directory, 2. 'bin' subdirectory
    search_paths = [base_dir, os.path.join(base_dir, 'bin'), os.path.join(base_dir, 'ffmpeg')]
    for path in search_paths:
        exe = os.path.join(path, 'ffmpeg.exe')
        if os.path.exists(exe):
            return os.path.abspath(exe)
            
    # Check system PATH
    try:
        if subprocess.run(['ffmpeg', '-version'], capture_output=True).returncode == 0:
            return 'ffmpeg'
    except: pass
        
    return None

def parse_time(time_str):
    """Converts hh:mm:ss or mm:ss into total seconds for yt-dlp."""
    if not time_str or time_str.strip().lower() == "end":
        return None
    parts = time_str.strip().split(':')
    parts.reverse()
    total_seconds = 0
    for i, part in enumerate(parts):
        total_seconds += int(part) * (60 ** i)
    return total_seconds


class PlaylistCrawlerDialog(ctk.CTkToplevel):
    def __init__(self, master, on_add_callback, playlist_title="Crawling Link...", entries=None):
        super().__init__(master)
        C = DownloadManagerApp.COLORS
        self.title(playlist_title)
        self.entries = entries
        self.on_add_callback = on_add_callback
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *a: self.refresh_view())
        self._is_loading = False
        
        self.configure(fg_color=C["bg"])
        self._center_window(1050, 800)
        
        self.after(100, self.lift)
        self.after(100, self.focus_force)
        self.after(100, self.grab_set)

        # 1. Loading Frame
        self.loading_frame = ctk.CTkFrame(self, fg_color=C["bg"])
        self.spinner_label = ctk.CTkLabel(self.loading_frame, text="⠋", font=("Segoe UI", 60),
                                          text_color=C["accent"])
        self.spinner_label.pack(pady=(220, 20))
        
        ctk.CTkLabel(self.loading_frame, text="Extracting metadata...",
                     font=("Segoe UI", 18, "bold"), text_color=C["text"]).pack(pady=10)
        ctk.CTkLabel(self.loading_frame, text="Please wait while we fetch the video list",
                     font=("Segoe UI", 13), text_color=C["text_dim"]).pack()
        
        # 2. Main Container
        self.main_container = ctk.CTkFrame(self, fg_color=C["bg"])
        
        # Header
        header = ctk.CTkFrame(self.main_container, corner_radius=0, fg_color=C["surface"])
        header.pack(side=tk.TOP, fill=tk.X)
        self.title_label = ctk.CTkLabel(header, text="Playlist Browser",
                                        font=("Segoe UI", 20, "bold"), text_color=C["text"])
        self.title_label.pack(pady=(14, 4))
        self.count_label = ctk.CTkLabel(header, text="", font=("Segoe UI", 12),
                                        text_color=C["text_dim"])
        self.count_label.pack(pady=(0, 10))
        
        # Control Bar
        ctrl_bar = ctk.CTkFrame(self.main_container, fg_color="transparent")
        ctrl_bar.pack(fill=tk.X, padx=20, pady=12)
        
        for txt, cmd in [("Select All", self.select_all), ("Unselect All", self.unselect_all)]:
            b = ctk.CTkButton(ctrl_bar, text=txt, width=95, height=28,
                              font=("Segoe UI", 11), command=cmd, corner_radius=6,
                              fg_color=C["surface_hover"], hover_color=C["border"],
                              text_color=C["text_dim"])
            b.pack(side=tk.LEFT, padx=(0, 5))
        
        # Search
        search_frame = ctk.CTkFrame(ctrl_bar, corner_radius=8, fg_color=C["surface_hover"],
                                    border_width=1, border_color=C["border"])
        search_frame.pack(side=tk.RIGHT, padx=5)
        ctk.CTkLabel(search_frame, text="🔍", font=("Segoe UI", 11)).pack(side=tk.LEFT, padx=(10, 4))
        ctk.CTkEntry(search_frame, textvariable=self.search_var,
                     placeholder_text="Filter playlist...", width=180, height=26,
                     border_width=0, fg_color="transparent", font=("Segoe UI", 11),
                     text_color=C["text"], placeholder_text_color=C["text_dim"]).pack(side=tk.LEFT, padx=(0, 10))

        # List Frame
        list_frame = ctk.CTkFrame(self.main_container, corner_radius=10, fg_color=C["bg"],
                                  border_width=1, border_color=C["border"])
        list_frame.pack(fill=tk.BOTH, expand=True, padx=20, pady=5)
        
        style = ttk.Style()
        style.configure("Playlist.Treeview", background=C["treeview_bg"],
                         foreground=C["text"], rowheight=36,
                         fieldbackground=C["treeview_bg"], borderwidth=0)
        style.map("Playlist.Treeview",
                   background=[('selected', C["treeview_sel"])],
                   foreground=[('selected', '#FFFFFF')])
        
        columns = ("Sel", "#", "Title")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", style="Playlist.Treeview")
        self.tree.heading("Sel", text="✓")
        self.tree.column("Sel", width=50, anchor="center", stretch=False)
        self.tree.heading("#", text="#")
        self.tree.column("#", width=50, anchor="center", stretch=False)
        self.tree.heading("Title", text="Video Title")
        self.tree.column("Title", width=500, stretch=True)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=8, pady=8)
        scroll = ctk.CTkScrollbar(list_frame, command=self.tree.yview,
                                  button_color=C["border"], button_hover_color=C["text_dim"])
        scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=8)
        self.tree.configure(yscroll=scroll.set)
        self.tree.bind("<ButtonRelease-1>", self.on_click)
        
        # Footer
        footer = ctk.CTkFrame(self.main_container, fg_color="transparent")
        footer.pack(fill=tk.X, padx=20, pady=16)
        
        self.add_btn = ctk.CTkButton(footer, text="Add Selected to Queue",
                                     command=self.add_selected,
                                     font=("Segoe UI", 13, "bold"), height=40,
                                     corner_radius=8, fg_color=C["accent"],
                                     hover_color=DownloadManagerApp._darken(C["accent"]))
        self.add_btn.pack(side=tk.RIGHT)
        ctk.CTkButton(footer, text="Cancel", command=self.destroy,
                      border_width=1, width=100, corner_radius=8,
                      fg_color="transparent", border_color=C["border"],
                      text_color=C["text_dim"],
                      hover_color=C["surface_hover"]).pack(side=tk.RIGHT, padx=10)
        
        if self.entries:
            self.show_entries(playlist_title, self.entries)
        else:
            self.show_loading()

    def _center_window(self, width, height):
        self.update_idletasks()
        master_x = self.master.winfo_rootx()
        master_y = self.master.winfo_rooty()
        master_width = self.master.winfo_width()
        master_height = self.master.winfo_height()
        x = master_x + (master_width // 2) - (width // 2)
        y = master_y + (master_height // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(width, height)

    def refresh_view(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        
        query = self.search_var.get().lower()
        if not self.entries: return
        for i, entry in enumerate(self.entries):
            title = entry.get('title', 'Unknown Title')
            if query in title.lower():
                sel = "☑" if self.selected_states.get(i, False) else "â˜"
                self.tree.insert("", "end", iid=str(i), values=(sel, i+1, title))

    def on_click(self, event):
        item = self.tree.identify_row(event.y)
        column = self.tree.identify_column(event.x)
        if item and column == "#1": # Select column
            idx = int(item)
            self.selected_states[idx] = not self.selected_states.get(idx, False)
            self.refresh_view()

    def select_all(self):
        self.selected_states = {i: True for i in range(len(self.entries))}
        self.refresh_view()

    def unselect_all(self):
        self.selected_states = {i: False for i in range(len(self.entries))}
        self.refresh_view()

    def add_selected(self):
        selected_entries = [self.entries[i] for i, state in self.selected_states.items() if state]
        if not selected_entries:
            messagebox.showwarning("Empty Selection", "Please select at least one video.")
            return
        
        self.on_add_callback(selected_entries)
        self.destroy()

    def show_loading(self):
        self._is_loading = True
        self.main_container.pack_forget()
        self.loading_frame.pack(fill=tk.BOTH, expand=True)
        self._animate_spinner()


    def _animate_spinner(self, idx=0):
        if not self._is_loading or not self.winfo_exists(): return
        chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠼"]
        self.spinner_label.configure(text=chars[idx % len(chars)])
        self.after(80, lambda: self._animate_spinner(idx + 1))

    def show_entries(self, title, entries):
        self._is_loading = False
        self.entries = entries
        self.title(f"Playlist: {title}")
        self.title_label.configure(text=f"📋 {title}")
        self.count_label.configure(text=f"{len(self.entries)} items found")
        self.selected_states = {i: True for i in range(len(self.entries))}
        
        self.loading_frame.pack_forget()
        self.main_container.pack(fill=tk.BOTH, expand=True)
        self.refresh_view()

class RangeSlider(ctk.CTkCanvas):
    def __init__(self, master, min_val=0, max_val=100, start_val=None, end_val=None, command=None, **kwargs):
        super().__init__(master, height=30, bg="#121212", highlightthickness=0, **kwargs)
        self.min_val = min_val
        self.max_val = max_val
        self.start_val = start_val if start_val is not None else min_val
        self.end_val = end_val if end_val is not None else max_val
        self.command = command
        
        self.bind("<Configure>", lambda e: self.draw())
        self.bind("<Button-1>", self.on_press)
        self.bind("<B1-Motion>", self.on_drag)
        
        self.active_handle = None

    def draw(self):
        self.delete("all")
        w = self.winfo_width()
        h = self.winfo_height()
        
        # Track
        self.create_rounded_rect(10, h//2-2, w-10, h//2+2, fill="#333333", outline="")
        
        # Selection
        x1 = self.val_to_x(self.start_val)
        x2 = self.val_to_x(self.end_val)
        self.create_rectangle(x1, h//2-3, x2, h//2+3, fill="#10B981", outline="")
        
        # Handles
        self.create_oval(x1-8, h//2-8, x1+8, h//2+8, fill="#FFFFFF", outline="#10B981", width=2, tags="start")
        self.create_oval(x2-8, h//2-8, x2+8, h//2+8, fill="#FFFFFF", outline="#10B981", width=2, tags="end")

    def create_rounded_rect(self, x1, y1, x2, y2, radius=3, **kwargs):
        points = [x1+radius, y1, x1+radius, y1, x2-radius, y1, x2-radius, y1, x2, y1, x2, y1+radius, x2, y1+radius, x2, y2-radius, x2, y2-radius, x2, y2, x2-radius, y2, x2-radius, y2, x1+radius, y2, x1+radius, y2, x1, y2, x1, y2-radius, x1, y2-radius, x1, y1+radius, x1, y1+radius, x1, y1]
        return self.create_polygon(points, smooth=True, **kwargs)

    def val_to_x(self, val):
        w = self.winfo_width() - 20
        if self.max_val == self.min_val: return 10
        return 10 + (val - self.min_val) / (self.max_val - self.min_val) * w

    def x_to_val(self, x):
        w = self.winfo_width() - 20
        val = self.min_val + (x - 10) / w * (self.max_val - self.min_val)
        return max(self.min_val, min(self.max_val, val))

    def on_press(self, event):
        x1 = self.val_to_x(self.start_val)
        x2 = self.val_to_x(self.end_val)
        if abs(event.x - x1) < 15: self.active_handle = "start"
        elif abs(event.x - x2) < 15: self.active_handle = "end"
        else: self.active_handle = None

    def on_drag(self, event):
        if not self.active_handle: return
        val = self.x_to_val(event.x)
        if self.active_handle == "start":
            self.start_val = min(val, self.end_val - (self.max_val - self.min_val) * 0.01)
        else:
            self.end_val = max(val, self.start_val + (self.max_val - self.min_val) * 0.01)
        
        self.draw()
        if self.command: self.command(self.start_val, self.end_val)

class MediaTrimmerDialog(ctk.CTkToplevel):
    def __init__(self, master, title, duration_secs, on_save_callback):
        super().__init__(master)
        self.title(f"âœ‚ï¸ Visual Trimmer: {title}")
        self.duration_secs = duration_secs
        self.on_save_callback = on_save_callback
        
        self.configure()
        self._center_window(750, 580)
        self.transient(master)
        self.grab_set()

        # Header
        ctk.CTkLabel(self, text="Select Media Range", font=("Segoe UI", 24, "bold")).pack(pady=(20, 5))
        self.title_label = ctk.CTkLabel(self, text=title, font=("Segoe UI", 12), wraplength=500)
        self.title_label.pack(pady=(0, 20))

        # Main Slider Area
        slider_frame = ctk.CTkFrame(self, corner_radius=12, border_width=1)
        slider_frame.pack(fill=tk.X, padx=30, pady=10)

        self.range_slider = RangeSlider(slider_frame, min_val=0, max_val=duration_secs, command=self.on_slider_move)
        self.range_slider.pack(fill=tk.X, padx=20, pady=30)
        
        # Time Inputs
        input_frame = ctk.CTkFrame(self)
        input_frame.pack(fill=tk.X, padx=30, pady=10)
        
        # Start
        s_box = ctk.CTkFrame(input_frame, corner_radius=8)
        s_box.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 10))
        ctk.CTkLabel(s_box, text="START", font=("Segoe UI", 10, "bold")).pack(pady=(5, 0))
        self.start_var = tk.StringVar(value="00:00:00")
        self.start_entry = ctk.CTkEntry(s_box, textvariable=self.start_var, width=100, border_width=0, justify="center", font=("Segoe UI", 16, "bold"))
        self.start_entry.pack(pady=(0, 5))
        
        # End
        e_box = ctk.CTkFrame(input_frame, corner_radius=8)
        e_box.pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(10, 0))
        ctk.CTkLabel(e_box, text="END", font=("Segoe UI", 10, "bold")).pack(pady=(5, 0))
        self.end_var = tk.StringVar(value=self.format_time(duration_secs))
        self.end_entry = ctk.CTkEntry(e_box, textvariable=self.end_var, width=100, border_width=0, justify="center", font=("Segoe UI", 16, "bold"))
        self.end_entry.pack(pady=(0, 5))

        # Duration Label
        self.duration_label = ctk.CTkLabel(self, text=f"Selected: {self.format_time(duration_secs)}", font=("Segoe UI", 13))
        self.duration_label.pack(pady=10)

        # Footer Actions
        footer = ctk.CTkFrame(self)
        footer.pack(fill=tk.X, side=tk.BOTTOM, padx=30, pady=20)
        
        ctk.CTkButton(footer, text="Save Range", command=self.save, font=("Segoe UI", 13, "bold"), height=38).pack(side=tk.RIGHT)
        ctk.CTkButton(footer, text="Cancel", command=self.destroy, border_width=1, width=100, height=38).pack(side=tk.RIGHT, padx=15)

    def on_slider_move(self, start, end):
        self.start_var.set(self.format_time(int(start)))
        self.end_var.set(self.format_time(int(end)))
        self.duration_label.configure(text=f"Selected: {self.format_time(int(end - start))}")

    def format_time(self, seconds):
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def parse_time(self, time_str):
        try:
            parts = list(map(int, time_str.split(':')))
            if len(parts) == 3: return parts[0]*3600 + parts[1]*60 + parts[2]
            if len(parts) == 2: return parts[0]*60 + parts[1]
            return parts[0]
        except: return 0

    def save(self):
        start_s = self.parse_time(self.start_var.get())
        end_s = self.parse_time(self.end_var.get())
        if end_s <= start_s:
            messagebox.showwarning("Invalid Range", "End time must be greater than start time.")
            return
        
        range_str = f"*{self.start_var.get()}-{self.end_var.get()}"
        self.on_save_callback(range_str)
        self.destroy()

    def _center_window(self, width, height):
        self.update_idletasks()
        master_x = self.master.winfo_rootx()
        master_y = self.master.winfo_rooty()
        master_width = self.master.winfo_width()
        master_height = self.master.winfo_height()
        x = master_x + (master_width // 2) - (width // 2)
        y = master_y + (master_height // 2) - (height // 2)
        self.geometry(f"{width}x{height}+{x}+{y}")
        self.minsize(width, height)

class DownloadManagerApp:
    # --- Modern Design System ---
    COLORS = {
        "bg":             "#0D1117",
        "surface":        "#161B22",
        "surface_hover":  "#1C2333",
        "border":         "#30363D",
        "accent":         "#3B82F6",
        "accent_hover":   "#2563EB",
        "success":        "#22C55E",
        "warning":        "#F59E0B",
        "danger":         "#EF4444",
        "purple":         "#8B5CF6",
        "text":           "#E6EDF3",
        "text_dim":       "#8B949E",
        "treeview_bg":    "#0D1117",
        "treeview_alt":   "#131921",
        "treeview_sel":   "#1A3A5C",
        "heading_bg":     "#161B22",
    }

    def __init__(self, root):
        self.root = root

        self.root.title("Media Downloader Pro")
        self.root.geometry("1300x850") 
        self.root.minsize(1300, 850)
        self.root.configure(fg_color=self.COLORS["bg"])
        
        # Font hierarchy
        self.font_main  = ("Segoe UI", 13)
        self.font_bold  = ("Segoe UI", 13, "bold")
        self.font_small = ("Segoe UI", 11)
        self.font_tiny  = ("Segoe UI", 10)
        self.font_title = ("Segoe UI", 18, "bold")
        self.font_heading = ("Segoe UI", 14, "bold")
        
        # Core Settings
        self.db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloader_history.db")
        self.init_db()

        self.download_folder = self.get_setting("download_folder", os.path.join(os.path.expanduser("~"), "Downloads"))
        self.embed_metadata = tk.BooleanVar(value=self.get_setting("embed_metadata", "True") == "True") 
        self.download_subs = tk.BooleanVar(value=self.get_setting("download_subs", "False") == "True")
        self.shutdown_pc = tk.BooleanVar(value=self.get_setting("shutdown_pc", "False") == "True")
        self.speed_limit = tk.StringVar(value=self.get_setting("speed_limit", "0")) 
        self.browser_cookie = tk.StringVar(value=self.get_setting("browser_cookie", "None"))
        self.concurrent_downloads = tk.StringVar(value=self.get_setting("concurrent_downloads", "1")) 
        self.minimize_to_tray = tk.BooleanVar(value=self.get_setting("minimize_to_tray", "False") == "True" and TRAY_AVAILABLE)
        self.notifications_enabled = tk.BooleanVar(value=self.get_setting("notifications_enabled", "True") == "True" and NOTIFICATIONS_AVAILABLE)
        self.use_aria2 = tk.BooleanVar(value=self.get_setting("use_aria2", "False") == "True")
        self.proxy_url = tk.StringVar(value=self.get_setting("proxy_url", ""))
        
        # Search & Filter
        self.search_query = tk.StringVar()
        self.search_query.trace_add("write", lambda *a: self.load_history_from_db())
        
        # FFmpeg Presets
        self.PRESET_MAP = {
            "⚡ Ultra Fast (Low Quality)": "ultrafast",
            "🚀 Fast (Good Balance)": "fast", 
            "⚖️ Medium (Default)": "medium",
            "🎬 Slow (High Quality)": "slow",
            "💎 Very Slow (Best Quality)": "veryslow"
        }
        self.ffmpeg_preset = tk.StringVar(value="⚖️ Medium (Default)")
        
        # Download State Tracking
        self.active_downloads = {}
        self.stop_all_flag = False
        self.metadata_cache = {}
        self.download_lock = threading.Lock()
        self.completed_items = 0
        self.total_items = 0
        self.is_dragging = False
        
        # Dashboard State
        self.active_speeds = {}
        self.active_downloaded = {}
        self.session_downloaded_finished = 0
        self.speed_history = [0] * 60
        self.peak_speed = 0
        self.current_speed_avg = 0
        self.session_start_time = time.time()
        
        # Tray icon
        self.tray_icon = None
        
        # Build the UI
        self.setup_ui()
        
        # Load saved history into the tree
        self.load_history_from_db()
        
        # Start dashboard updater
        self.update_dashboard_stats()
        
        # Start remote server
        self.start_remote_server()
        
        # Handle window events
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Unmap>", self.on_minimize)

    def setup_ui(self):
        C = self.COLORS
        
        # ═══════════════════════════════════════════════════════════
        #  TOOLBAR  — Top action bar
        # ═══════════════════════════════════════════════════════════
        toolbar = ctk.CTkFrame(self.root, corner_radius=0, border_width=0, fg_color=C["surface"])
        toolbar.pack(side=tk.TOP, fill=tk.X)
        
        # App branding
        brand = ctk.CTkLabel(toolbar, text="  ▼  Media Downloader Pro", font=("Segoe UI", 16, "bold"),
                             text_color=C["accent"])
        brand.pack(side=tk.LEFT, padx=(15, 25), pady=12)

        toolbar_btns = [
            ("+  New Link",        self.add_single,  C["accent"]),
            ("🎬  Channel Crawl",  self.add_channel,  C["purple"]),
            ("📂  Batch Add",      self.add_batch,    C["success"]),
        ]
        for text, cmd, color in toolbar_btns:
            btn = ctk.CTkButton(toolbar, text=text, command=cmd, font=self.font_bold,
                                height=36, corner_radius=8, fg_color=color,
                                hover_color=self._darken(color, 0.15), width=150)
            btn.pack(side=tk.LEFT, padx=4, pady=10)

        # Settings button — right-aligned, subtle
        btn_settings = ctk.CTkButton(toolbar, text="⚙  Settings", command=self.open_settings,
                                     font=self.font_bold, height=36, corner_radius=8,
                                     fg_color="transparent", border_width=1,
                                     border_color=C["border"], text_color=C["text_dim"],
                                     hover_color=C["surface_hover"], width=120)
        btn_settings.pack(side=tk.RIGHT, padx=15, pady=10)

        # ═══════════════════════════════════════════════════════════
        #  MEDIA CONFIG  — Type / Format / Bitrate / Preset
        # ═══════════════════════════════════════════════════════════
        config_bar = ctk.CTkFrame(self.root, corner_radius=10, fg_color=C["surface"],
                                  border_width=1, border_color=C["border"])
        config_bar.pack(side=tk.TOP, fill=tk.X, padx=18, pady=(10, 6))

        # --- Type selector ---
        ctk.CTkLabel(config_bar, text="TYPE", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(side=tk.LEFT, padx=(18, 8), pady=12)
        
        self.media_type = tk.StringVar(value="Audio")
        for val in ["Audio", "Video", "Thumbnail"]:
            r = ctk.CTkRadioButton(config_bar, text=val, variable=self.media_type, value=val,
                                   command=self.update_format_options, font=self.font_main,
                                   radiobutton_width=16, radiobutton_height=16,
                                   fg_color=C["accent"], hover_color=C["accent_hover"],
                                   border_color=C["border"], text_color=C["text"])
            r.pack(side=tk.LEFT, padx=8)

        # Vertical separator
        sep1 = ctk.CTkFrame(config_bar, width=1, height=28, fg_color=C["border"])
        sep1.pack(side=tk.LEFT, padx=14)

        # --- Format ---
        ctk.CTkLabel(config_bar, text="FORMAT", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(side=tk.LEFT, padx=(0, 6))
        self.format_var = tk.StringVar()
        self.format_combo = ctk.CTkComboBox(config_bar, variable=self.format_var, state="readonly",
                                            width=110, font=self.font_main, dropdown_font=self.font_main,
                                            fg_color=C["surface_hover"], border_color=C["border"],
                                            button_color=C["accent"], button_hover_color=C["accent_hover"])
        self.format_combo.pack(side=tk.LEFT, padx=4)
        self.format_var.trace_add("write", self.auto_update_all)

        # --- Bitrate ---
        self.bitrate_label = ctk.CTkLabel(config_bar, text="BITRATE", font=self.font_tiny,
                                          text_color=C["text_dim"])
        self.bitrate_label.pack(side=tk.LEFT, padx=(14, 6))
        self.bitrate_var = tk.StringVar(value="320")
        self.bitrate_combo = ctk.CTkComboBox(config_bar, variable=self.bitrate_var,
                                             values=["320", "256", "192", "128"], state="readonly",
                                             width=80, font=self.font_main, dropdown_font=self.font_main,
                                             fg_color=C["surface_hover"], border_color=C["border"],
                                             button_color=C["accent"], button_hover_color=C["accent_hover"])
        self.bitrate_combo.pack(side=tk.LEFT, padx=4)
        self.bitrate_var.trace_add("write", self.auto_update_all)

        # Vertical separator
        sep2 = ctk.CTkFrame(config_bar, width=1, height=28, fg_color=C["border"])
        sep2.pack(side=tk.LEFT, padx=14)

        # --- Processing Preset ---
        ctk.CTkLabel(config_bar, text="PROCESSING", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(side=tk.LEFT, padx=(0, 6))
        self.quality_combo = ctk.CTkComboBox(config_bar, values=list(self.PRESET_MAP.keys()),
                                             variable=self.ffmpeg_preset, width=200,
                                             font=self.font_main, dropdown_font=self.font_main,
                                             fg_color=C["surface_hover"], border_color=C["border"],
                                             button_color=C["accent"], button_hover_color=C["accent_hover"])
        self.quality_combo.pack(side=tk.LEFT, padx=(4, 18))

        # ═══════════════════════════════════════════════════════════
        #  LEFT SIDEBAR  — Controls
        # ═══════════════════════════════════════════════════════════
        sidebar = ctk.CTkFrame(self.root, width=230, corner_radius=12,
                               fg_color=C["surface"], border_width=1, border_color=C["border"])
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(18, 0), pady=(0, 18))
        sidebar.pack_propagate(False)

        # Section label
        ctk.CTkLabel(sidebar, text="CONTROLS", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(anchor="w", padx=18, pady=(20, 8))

        # Transport Controls
        transport_items = [
            ("▶  Resume",   self.resume_selected,  C["success"],  "#1A7F3E"),
            ("⏸  Pause",    self.pause_selected,   C["warning"],  "#C27D08"),
            ("⏹  Stop All", self.stop_all,         C["danger"],   "#C93636"),
        ]
        for text, cmd, color, hover in transport_items:
            btn = ctk.CTkButton(sidebar, text=text, command=cmd, font=self.font_bold,
                                height=40, corner_radius=8, fg_color=color, hover_color=hover)
            btn.pack(padx=14, pady=4, fill=tk.X)

        # Divider
        ctk.CTkFrame(sidebar, height=1, fg_color=C["border"]).pack(fill=tk.X, padx=24, pady=16)

        # Queue Management
        ctk.CTkLabel(sidebar, text="QUEUE", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(anchor="w", padx=18, pady=(0, 8))
        
        mgmt_items = [
            ("🗑  Remove",  self.remove_selected),
            ("✕  Clear All", self.clear_all),
        ]
        for text, cmd in mgmt_items:
            btn = ctk.CTkButton(sidebar, text=text, command=cmd, font=self.font_main,
                                height=34, corner_radius=8, fg_color="transparent",
                                border_width=1, border_color=C["border"],
                                text_color=C["text_dim"], hover_color=C["surface_hover"],
                                anchor="w")
            btn.pack(padx=14, pady=3, fill=tk.X)

        # --- Bottom action area ---
        action_frame = ctk.CTkFrame(sidebar, fg_color="transparent")
        action_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=14, pady=18)

        btn_dl_sel = ctk.CTkButton(action_frame, text="DOWNLOAD SELECTED",
                                   command=self.download_selected, font=self.font_bold,
                                   height=44, corner_radius=10, fg_color=C["accent"],
                                   hover_color=C["accent_hover"])
        btn_dl_sel.pack(fill=tk.X, pady=(0, 6))

        btn_dl_all = ctk.CTkButton(action_frame, text="DOWNLOAD ALL",
                                   command=self.download_all, font=self.font_bold,
                                   height=44, corner_radius=10, fg_color="transparent",
                                   border_width=1, border_color=C["accent"],
                                   text_color=C["accent"], hover_color=C["surface_hover"])
        btn_dl_all.pack(fill=tk.X)

        # ═══════════════════════════════════════════════════════════
        #  MAIN CONTENT  — Tabview with Queue & Dashboard
        # ═══════════════════════════════════════════════════════════
        self.main_tabview = ctk.CTkTabview(self.root, fg_color=C["surface"],
                                           segmented_button_fg_color=C["surface_hover"],
                                           segmented_button_selected_color=C["accent"],
                                           segmented_button_selected_hover_color=C["accent_hover"],
                                           segmented_button_unselected_color=C["surface_hover"],
                                           segmented_button_unselected_hover_color=C["surface_hover"],
                                           border_width=1, border_color=C["border"],
                                           corner_radius=12)
        self.main_tabview.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=18, pady=(6, 0))
        
        self.tab_queue = self.main_tabview.add("  📥  Download Queue  ")
        self.tab_stats = self.main_tabview.add("  📊  Live Dashboard  ")
        self.tab_queue.configure(fg_color=C["surface"])
        self.tab_stats.configure(fg_color=C["surface"])

        # ─── QUEUE TAB ────────────────────────────────────────────
        list_container = ctk.CTkFrame(self.tab_queue, fg_color="transparent")
        list_container.pack(fill=tk.BOTH, expand=True)

        # Toolbar row: Select All / Unselect All / Search / Refresh
        sel_bar = ctk.CTkFrame(list_container, fg_color="transparent")
        sel_bar.pack(fill=tk.X, pady=(4, 6))
        
        for txt, cmd in [("Select All", self.select_all_items), ("Unselect All", self.unselect_all_items)]:
            b = ctk.CTkButton(sel_bar, text=txt, width=95, height=28, font=self.font_small,
                              command=cmd, corner_radius=6, fg_color=C["surface_hover"],
                              hover_color=C["border"], text_color=C["text_dim"])
            b.pack(side=tk.LEFT, padx=(0, 5))

        btn_refresh = ctk.CTkButton(sel_bar, text="↻", width=32, height=28, font=("Segoe UI", 15),
                                    command=self.refresh_list, corner_radius=6,
                                    fg_color=C["surface_hover"], hover_color=C["border"],
                                    text_color=C["text_dim"])
        btn_refresh.pack(side=tk.RIGHT, padx=2)

        # Search bar
        search_frame = ctk.CTkFrame(sel_bar, corner_radius=8, fg_color=C["surface_hover"],
                                    border_width=1, border_color=C["border"])
        search_frame.pack(side=tk.RIGHT, padx=8)
        
        ctk.CTkLabel(search_frame, text="🔍", font=self.font_small).pack(side=tk.LEFT, padx=(10, 4))
        self.search_entry = ctk.CTkEntry(search_frame, textvariable=self.search_query,
                                         placeholder_text="Search downloads…", 
                                         width=200, height=26, border_width=0,
                                         fg_color="transparent", font=self.font_small,
                                         text_color=C["text"], placeholder_text_color=C["text_dim"])
        self.search_entry.pack(side=tk.LEFT, padx=(0, 10))

        # ─── TREEVIEW ─────────────────────────────────────────────
        list_frame = ctk.CTkFrame(list_container, corner_radius=10, fg_color=C["bg"],
                                  border_width=1, border_color=C["border"])
        list_frame.pack(fill=tk.BOTH, expand=True)

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview",
                         background=C["treeview_bg"],
                         foreground=C["text"],
                         rowheight=36,
                         fieldbackground=C["treeview_bg"],
                         borderwidth=0,
                         font=self.font_main)
        style.map('Treeview',
                   background=[('selected', C["treeview_sel"])],
                   foreground=[('selected', '#FFFFFF')])
        style.configure("Treeview.Heading",
                         background=C["heading_bg"],
                         foreground=C["text_dim"],
                         relief="flat",
                         font=self.font_small,
                         padding=(0, 8))
        style.map("Treeview.Heading", background=[('active', C["heading_bg"])])
        
        columns = ("Sel", "#", "Title", "URL", "Range", "Type", "Size", "Status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        self.tree.tag_configure('oddrow',  background=C["treeview_bg"])
        self.tree.tag_configure('evenrow', background=C["treeview_alt"])
        
        col_defs = [
            ("Sel",    "✓",           50,  50,  "center", False),
            ("#",      "#",           40,  40,  "center", False),
            ("Title",  "Title",       300, 150, "w",      True),
            ("URL",    "URL",         140, 100, "w",      True),
            ("Range",  "Range",       90,  80,  "center", False),
            ("Type",   "Type / Qual", 120, 110, "center", False),
            ("Size",   "Est. Size",   80,  70,  "center", False),
            ("Status", "Status",      140, 130, "center", False),
        ]
        for col_id, heading, width, minw, anchor, stretch in col_defs:
            self.tree.heading(col_id, text=heading)
            self.tree.column(col_id, width=width, minwidth=minw, anchor=anchor, stretch=stretch)

        tree_scroll = ctk.CTkScrollbar(list_frame, command=self.tree.yview,
                                       button_color=C["border"], button_hover_color=C["text_dim"])
        self.tree.configure(yscroll=tree_scroll.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 6), pady=8)

        self.tree.bind("<Delete>", lambda e: self.remove_selected())
        self.tree.bind("<ButtonPress-1>", self.on_tree_press)
        self.tree.bind("<B1-Motion>", self.on_tree_drag)
        self.tree.bind("<ButtonRelease-1>", self.on_tree_click)
        self.tree.bind("<Button-3>", self.show_context_menu)

        # ─── DASHBOARD TAB ────────────────────────────────────────
        self.setup_dashboard_ui()

        # ═══════════════════════════════════════════════════════════
        #  BOTTOM STATUS BAR
        # ═══════════════════════════════════════════════════════════
        bottom_frame = ctk.CTkFrame(self.root, corner_radius=10, fg_color=C["surface"],
                                    border_width=1, border_color=C["border"])
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=18, pady=(6, 18))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ctk.CTkProgressBar(bottom_frame, variable=self.progress_var,
                                               height=6, corner_radius=3,
                                               progress_color=C["accent"],
                                               fg_color=C["surface_hover"])
        self.progress_bar.pack(fill=tk.X, padx=20, pady=(14, 8))
        self.progress_bar.set(0) 

        stats_row = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        stats_row.pack(fill=tk.X, padx=20, pady=(0, 4))

        self.status_label = ctk.CTkLabel(stats_row, text="Ready", font=self.font_bold,
                                         text_color=C["text"])
        self.status_label.pack(side=tk.LEFT)

        self.location_label = ctk.CTkLabel(bottom_frame, text=f"📂  {self.download_folder}",
                                           font=self.font_small, text_color=C["text_dim"])
        self.location_label.pack(anchor="w", padx=20, pady=(0, 12))

        self.update_format_options()

        # Global Key Bindings
        self.root.bind("<Control-a>", self.on_ctrl_a)
        self.root.bind("<Control-v>", self.on_ctrl_v)

    def on_ctrl_a(self, event):
        # Allow standard Select All in entry fields
        if isinstance(self.root.focus_get(), (ctk.CTkEntry, ctk.CTkTextbox, tk.Entry, tk.Text)):
            return
        # Perform UI selection (blue highlight) instead of checking boxes
        self.tree.selection_set(self.tree.get_children())
        return "break"

    def on_ctrl_v(self, event):
        # Allow standard Paste in entry fields
        if isinstance(self.root.focus_get(), (ctk.CTkEntry, ctk.CTkTextbox, tk.Entry, tk.Text)):
            return
        try:
            url = self.root.clipboard_get()
            if url and "http" in url:
                self.fetch_and_add(url.strip(), "Full Video")
        except: pass
        return "break"

    @staticmethod
    def _darken(hex_color, factor=0.15):
        """Darken a hex color by a factor (0-1)."""
        hex_color = hex_color.lstrip('#')
        r, g, b = int(hex_color[:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
        r = max(0, int(r * (1 - factor)))
        g = max(0, int(g * (1 - factor)))
        b = max(0, int(b * (1 - factor)))
        return f"#{r:02X}{g:02X}{b:02X}"

    def _apply_cookies(self, ydl_opts):
        """Apply browser cookie settings to yt-dlp opts."""
        browser = self.browser_cookie.get()
        if browser != "None":
            ydl_opts['cookiesfrombrowser'] = (browser,)

    def start_remote_server(self):
        if not SERVER_AVAILABLE: return
        try:
            self.remote_server = RemoteServer(self)
            self.remote_thread = threading.Thread(target=self.remote_server.run, daemon=True)
            self.remote_thread.start()
            print("Remote Server started on port 5000")
        except Exception as e:
            print(f"Failed to start remote server: {e}")

    def get_local_ip(self):
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            return ip
        except:
            return "127.0.0.1"
            
    def show_remote_qr(self):
        ip = self.get_local_ip()
        url = f"http://{ip}:5000"
        
        qr_win = ctk.CTkToplevel(self.root)
        qr_win.title("Remote Control")
        qr_win.geometry("350x450")
        qr_win.resizable(False, False)
        qr_win.attributes('-topmost', True)
        qr_win.transient(self.root)
        
        ctk.CTkLabel(qr_win, text="Scan to Control from Phone", font=self.font_bold).pack(pady=(20, 10))
        
        try:
            qr = qrcode.QRCode(version=1, box_size=10, border=2)
            qr.add_data(url)
            qr.make(fit=True)
            img = qr.make_image()
            
            # Save temporary image to display in tkinter
            import tempfile
            import os
            fd, path = tempfile.mkstemp(suffix=".png")
            os.close(fd)
            img.save(path)
            
            from PIL import ImageTk
            photo = ImageTk.PhotoImage(file=path)
            lbl = tk.Label(qr_win, image=photo, bg="#121212")
            lbl.image = photo # keep reference
            lbl.pack(pady=10)
            
            # Cleanup temp file later
            qr_win.protocol("WM_DELETE_WINDOW", lambda: (os.remove(path) if os.path.exists(path) else None, qr_win.destroy()))
            
        except Exception as e:
            ctk.CTkLabel(qr_win, text=f"Could not generate QR code:\n{e}").pack(pady=20)
            
        ctk.CTkLabel(qr_win, text=f"Or visit on your phone's browser:\n{url}", font=self.font_main).pack(pady=(10, 20))

    # --- Database Methods ---
    def init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            # Settings Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )
            ''')
            # Downloads Table
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS downloads (
                    db_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sel TEXT,
                    title TEXT,
                    url TEXT,
                    time_range TEXT,
                    media_type TEXT,
                    status TEXT,
                    file_path TEXT,
                    timestamp TEXT
                )
            ''')
            
            # Migration: Add file_size column if it doesn't exist
            cursor.execute("PRAGMA table_info(downloads)")
            columns = [info[1] for info in cursor.fetchall()]
            if 'file_size' not in columns:
                try:
                    cursor.execute('ALTER TABLE downloads ADD COLUMN file_size TEXT')
                except Exception as e:
                    print(f"Migration Error: {e}")
            
            conn.commit()

    def save_setting(self, key, value):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)', (key, str(value)))
            conn.commit()

    def get_setting(self, key, default=None):
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
                return row[0] if row else default
        except Exception:
            return default

    def update_yt_dlp(self, button=None):
        """Runs pip install -U yt-dlp with a premium in-button spinner animation."""
        if hasattr(self, "_is_updating") and self._is_updating:
            return
            
        self._is_updating = True
        spinner_chars = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠼"]
        original_text = button.cget("text") if button else "Update"
        
        def animate_spinner(idx=0):
            if not self._is_updating:
                if button:
                    button.configure(text=original_text, state="normal")
                return
            if button:
                button.configure(text=spinner_chars[idx % len(spinner_chars)])
                self.root.after(80, lambda: animate_spinner(idx + 1))

        def run_update():
            self.root.after(0, lambda: self.status_label.configure(text="Updating..."))
            if button:
                self.root.after(0, lambda: button.configure(state="disabled"))
                self.root.after(0, lambda: animate_spinner())
            
            try:
                # Use sys.executable to ensure we use the same environment
                process = subprocess.Popen([sys.executable, "-m", "pip", "install", "-U", "yt-dlp"], 
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                stdout, stderr = process.communicate()
                
                if process.returncode == 0:
                    self.root.after(0, lambda: messagebox.showinfo("Success", "Updated successfully!"))
                    self.root.after(0, lambda: self.status_label.configure(text="Updated successfully."))
                else:
                    self.root.after(0, lambda: messagebox.showerror("Update Failed", "Update failed. Please check your connection."))
                    self.root.after(0, lambda: self.status_label.configure(text="Update failed."))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", f"An unexpected error occurred: {str(e)}"))
            finally:
                self._is_updating = False
        
        threading.Thread(target=run_update, daemon=True).start()

    def refresh_list(self):
        """Forces a sync between the DB and the Treeview, checking if files still exist."""
        updated = 0
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('SELECT db_id, file_path, status FROM downloads')
            rows = cursor.fetchall()
            
            for db_id, path, status in rows:
                new_status = status
                if status == "Done" or status == "File Missing":
                    # Normalize path for robust checking
                    norm_path = os.path.normpath(path).strip() if path else None
                    if norm_path and os.path.exists(norm_path):
                        new_status = "Done"
                    else:
                        new_status = "File Missing"
                
                if new_status != status:
                    cursor.execute('UPDATE downloads SET status=? WHERE db_id=?', (new_status, db_id))
                    updated += 1
            conn.commit()
        
        self.load_history_from_db()
        self.status_label.configure(text=f"List refreshed. {updated} items updated.")

    def update_db_item(self, values, db_id=None):
        # values: (sel, title, url, range, type, status, file_path)
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            if db_id:
                cursor.execute('''
                    UPDATE downloads SET sel=?, title=?, url=?, time_range=?, media_type=?, status=?, file_path=?, file_size=?
                    WHERE db_id=?
                ''', (*values, db_id))
            else:
                timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cursor.execute('''
                    INSERT INTO downloads (sel, title, url, time_range, media_type, status, file_path, file_size, timestamp)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (*values, timestamp))
                db_id = cursor.lastrowid
            conn.commit()
        return db_id

    def delete_db_item(self, db_id):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            cursor.execute('DELETE FROM downloads WHERE db_id=?', (db_id,))
            conn.commit()

    def load_history_from_db(self):
        for i in self.tree.get_children():
            self.tree.delete(i)
        
        query = self.search_query.get().strip()
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            sql = "SELECT db_id, sel, title, url, time_range, media_type, status, file_path, file_size, timestamp FROM downloads"
            if query:
                cursor.execute(sql + " WHERE title LIKE ? OR url LIKE ? OR status LIKE ?", (f'%{query}%', f'%{query}%', f'%{query}%'))
            else:
                cursor.execute(sql)
            
            rows = cursor.fetchall()
            
            for i, row in enumerate(rows):
                db_id, sel, title, url, time_range, media_type, status, file_path, file_size, timestamp = row
                idx = len(self.tree.get_children()) + 1
                tag = 'evenrow' if i % 2 == 0 else 'oddrow'
                
                # Default size to "---" if empty
                display_size = file_size if file_size else "---"
                
                # We store db_id in the 'text' attribute of the item for easy reference
                self.tree.insert("", "end", iid=f"db_{db_id}", 
                                 values=(sel, idx, title, url, time_range, media_type, display_size, status),
                                 tags=(tag,))
            
            # Auto-scroll to bottom on load
            children = self.tree.get_children()
            if children:
                self.tree.see(children[-1])

    # --- Context Menu Methods ---
    def show_context_menu(self, event):
        item = self.tree.identify_row(event.y)
        if item:
            self.tree.selection_set(item)
            
            menu_x = event.x_root
            menu_y = event.y_root
            
            # Identify if it's already a range
            current_range = self.tree.item(item, "values")[4]
            trim_label = "âœ‚ï¸ Trim Media (Visual)"
            if current_range != "Full Video":
                trim_label = "âœ‚ï¸ Edit Trim (Visual)"

            commands = [
                ("▶ Open File", self.ctx_open_file),
                ("ðŸ“ Open Folder", self.ctx_open_folder),
                "separator",
                (trim_label, self.ctx_trim_visual),
                ("🔄 Re-download", self.ctx_redownload),
                ("🗑 Delete from List", self.ctx_delete_list),
                ("🔥 Delete from Disk", self.ctx_delete_disk),
                "separator",
                ("ℹ Properties", self.ctx_properties)
            ]
            
            CTkContextMenu(self.root, menu_x, menu_y, commands)

    def ctx_trim_visual(self):
        # Verification: Check for FFmpeg first
        ffmpeg_bin = get_ffmpeg_path()
        if not ffmpeg_bin:
            messagebox.showerror("FFmpeg Missing", 
                "Advanced Trimming requires FFmpeg.\n\n"
                "Please place 'ffmpeg.exe' in the same folder as this app, "
                "or install it and add it to your system PATH.")
            return

        item = self.tree.selection()[0]
        url = self.tree.item(item, "values")[3]
        title = self.tree.item(item, "values")[2]
        db_id = int(item.replace("db_", ""))
        
        self.status_label.configure(text="Fetching duration for trimmer...")
        
        def fetch_duration():
            ydl_opts = {'quiet': True, 'extract_flat': True, 'no_warnings': True}
            self._apply_cookies(ydl_opts)
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    info = ydl.extract_info(url, download=False)
                    duration = info.get('duration')
                    if duration:
                        def launch():
                            MediaTrimmerDialog(self.root, title, duration, 
                                              lambda r: self.save_trim_result(item, db_id, r))
                        self.root.after(0, launch)
                        self.root.after(0, lambda: self.status_label.configure(text="Ready"))
                    else:
                        self.root.after(0, lambda: messagebox.showerror("Error", "Could not fetch media duration."))
            except Exception as e:
                self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to fetch metadata: {str(e)}"))
        
        threading.Thread(target=fetch_duration, daemon=True).start()

    def save_trim_result(self, item_id, db_id, range_str):
        # Update Treeview
        current_values = list(self.tree.item(item_id, "values"))
        current_values[4] = range_str # Range column
        current_values[7] = "Queued"  # Reset status to Queued
        self.tree.item(item_id, values=current_values)
        
        # Update Database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE downloads SET time_range=?, status="Queued" WHERE db_id=?', (range_str, db_id))
            conn.commit()
        
        self.status_label.configure(text=f"Range saved: {range_str}")

    def get_selected_db_id(self):
        selection = self.tree.selection()
        if not selection: return None
        item_id = selection[0]
        return int(item_id.replace("db_", ""))

    def ctx_open_file(self):
        db_id = self.get_selected_db_id()
        if not db_id: return
        with sqlite3.connect(self.db_path) as conn:
            path = conn.execute('SELECT file_path FROM downloads WHERE db_id=?', (db_id,)).fetchone()[0]
        if path and os.path.exists(path):
            os.startfile(path)
        else:
            messagebox.showerror("Error", "File not found or not yet downloaded.")

    def ctx_open_folder(self):
        db_id = self.get_selected_db_id()
        if not db_id: return
        with sqlite3.connect(self.db_path) as conn:
            path = conn.execute('SELECT file_path FROM downloads WHERE db_id=?', (db_id,)).fetchone()[0]
        if path and os.path.exists(path):
            os.system(f'explorer /select,"{os.path.normpath(path)}"')
        else:
            messagebox.showerror("Error", "Folder/File not found.")

    def ctx_redownload(self):
        selection = self.tree.selection()
        if not selection: return
        item_id = selection[0]
        db_id = int(item_id.replace("db_", ""))
        
        current_values = list(self.tree.item(item_id, "values"))
        current_values[7] = "Queued" # Status
        self.tree.item(item_id, values=current_values)
        
        # Update DB status
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE downloads SET status="Queued" WHERE db_id=?', (db_id,))
            conn.commit()
            
        self._start_download_batch([item_id])

    def ctx_delete_list(self):
        selection = self.tree.selection()
        if not selection: return
        for item_id in selection:
            db_id = int(item_id.replace("db_", ""))
            self.delete_db_item(db_id)
            self.tree.delete(item_id)

    def ctx_delete_disk(self):
        selection = self.tree.selection()
        if not selection: return
        if not messagebox.askyesno("Confirm", "Are you sure you want to delete these files from your computer?"):
            return
            
        for item_id in selection:
            db_id = int(item_id.replace("db_", ""))
            with sqlite3.connect(self.db_path) as conn:
                path = conn.execute('SELECT file_path FROM downloads WHERE db_id=?', (db_id,)).fetchone()[0]
            
            if path:
                # Normalize path
                norm_path = os.path.normpath(path).strip()
                if os.path.exists(norm_path):
                    try:
                        os.remove(norm_path)
                    except Exception as e:
                        messagebox.showerror("Error", f"Could not delete {norm_path}: {str(e)}")
                else:
                    messagebox.showwarning("File Not Found", f"Could not find file on disk: {norm_path}")
            
            self.delete_db_item(db_id)
            self.tree.delete(item_id)

    def ctx_properties(self):
        db_id = self.get_selected_db_id()
        if not db_id: return
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute('SELECT db_id, sel, title, url, time_range, media_type, status, file_path, file_size, timestamp FROM downloads WHERE db_id=?', (db_id,)).fetchone()
        
        if not row: return
        _, sel, title, url, time_range, media_type, status, file_path, file_size, timestamp = row
        
        prop_win = ctk.CTkToplevel(self.root)
        prop_win.title("Download Properties")
        self.center_toplevel(prop_win, 550, 450)
        prop_win.transient(self.root)
        
        ctk.CTkLabel(prop_win, text="Item Properties", font=self.font_title).pack(pady=20)
        
        form = ctk.CTkFrame(prop_win, corner_radius=10, border_width=1)
        form.pack(padx=20, fill=tk.BOTH, expand=True, pady=(0, 20))
        
        details = [
            ("Title:", title),
            ("URL:", url),
            ("Quality:", media_type),
            ("Range:", time_range),
            ("Status:", status),
            ("Est. Size:", file_size if file_size else "Unknown"),
            ("Saved To:", file_path if file_path else "Not downloaded yet"),
            ("Added On:", timestamp)
        ]
        
        for i, (label, val) in enumerate(details):
            ctk.CTkLabel(form, text=label, font=self.font_bold).grid(row=i, column=0, padx=15, pady=10, sticky="e")
            # Using Textbox for values to make them selectable/copyable
            v_box = ctk.CTkTextbox(form, height=30 if "\n" not in str(val) else 60, width=350, font=self.font_main)
            v_box.insert("1.0", str(val))
            v_box.configure(state="disabled")
            v_box.grid(row=i, column=1, padx=5, pady=10, sticky="w")

    def center_toplevel(self, window, width, height):
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (width // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (height // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

    def on_tree_press(self, event):
        self.drag_start_y = event.y
        self.is_dragging = False

    def on_tree_drag(self, event):
        delta = self.drag_start_y - event.y
        if abs(delta) > 5:
            self.is_dragging = True
            # Scroll behavior: positive delta moves content up (scroll down)
            if delta > 0:
                self.tree.yview_scroll(1, "units")
            else:
                self.tree.yview_scroll(-1, "units")
            self.drag_start_y = event.y
            self.tree.configure(cursor="fleur") # Change cursor to indicate moving

    def on_tree_click(self, event):
        self.tree.configure(cursor="") # Reset cursor
        if self.is_dragging:
            return # Don't toggle selection if we were just scrolling

        region = self.tree.identify_region(event.x, event.y)
        if region == "cell":
            column = self.tree.identify_column(event.x)
            item_id = self.tree.identify_row(event.y)
            if column == "#1": # Sel column
                current_values = list(self.tree.item(item_id, "values"))
                new_sel = "☑" if current_values[0] == "â˜" else "â˜"
                current_values[0] = new_sel
                self.tree.item(item_id, values=current_values)
                
                # Update DB
                db_id = int(item_id.replace("db_", ""))
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute('UPDATE downloads SET sel=? WHERE db_id=?', (new_sel, db_id))
                    conn.commit()

    def select_all_items(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE downloads SET sel="☑"')
            conn.commit()
        self.load_history_from_db()

    def unselect_all_items(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute('UPDATE downloads SET sel="â˜"')
            conn.commit()
        self.load_history_from_db()

    # --- UI Logic Methods ---
    def update_format_options(self, event=None):
        if self.media_type.get() == "Audio":
            self.format_combo.configure(values=["mp3", "wav", "m4a"])
            self.format_combo.set("mp3")
            self.bitrate_label.pack(side=tk.LEFT, padx=(10, 5))
            self.bitrate_combo.pack(side=tk.LEFT, padx=5)
        elif self.media_type.get() == "Video":
            self.format_combo.configure(values=["Best Quality", "4K", "1440p", "1080p", "720p", "480p", "360p", "240p"])
            self.format_combo.set("Best Quality")
            self.bitrate_label.pack_forget()
            self.bitrate_combo.pack_forget()
        else:
            self.format_combo.configure(values=["jpg", "png", "webp"])
            self.format_combo.set("jpg")
            self.bitrate_label.pack_forget()
            self.bitrate_combo.pack_forget()

    def auto_update_all(self, *args):
        if not hasattr(self, 'tree'): return
        all_items = self.tree.get_children()
        if not all_items: return 
            
        new_media_type = self.get_current_media_type_str()
        updated_count = 0
        
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.cursor()
            for item in all_items:
                db_id = int(item.replace("db_", ""))
                current_values = list(self.tree.item(item, "values"))
                current_status = current_values[7]
                
                if "Downloading" not in current_status and "Done" not in current_status:
                    # Update Treeview
                    current_values[5] = new_media_type
                    
                    # Try to get size from cache
                    new_size = "---"
                    if db_id in self.metadata_cache:
                        new_size = self.estimate_size(self.metadata_cache[db_id], new_media_type)
                    
                    current_values[6] = new_size
                    current_values[7] = "Queued"
                    self.tree.item(item, values=current_values)
                    
                    # Update DB
                    cursor.execute('UPDATE downloads SET media_type=?, status=?, file_size=? WHERE db_id=?', 
                                 (new_media_type, "Queued", new_size, db_id))
                    updated_count += 1
            conn.commit()
            
        if updated_count > 0:
            self.status_label.configure(text=f"Updated {updated_count} items to {new_media_type}.")

    def open_settings(self):
        C = self.COLORS
        settings_win = ctk.CTkToplevel(self.root)
        settings_win.title("Settings")
        settings_win.configure(fg_color=C["bg"])
        self.center_toplevel(settings_win, 820, 540)
        settings_win.transient(self.root)
        settings_win.grab_set() 

        # Header
        header = ctk.CTkFrame(settings_win, fg_color=C["surface"], corner_radius=0)
        header.pack(fill=tk.X)
        ctk.CTkLabel(header, text="⚙  Settings", font=self.font_title,
                     text_color=C["text"]).pack(pady=14, padx=20, anchor="w")

        # --- Main Container with 2 Columns ---
        main_container = ctk.CTkFrame(settings_win, fg_color="transparent")
        main_container.pack(fill=tk.BOTH, expand=True, padx=20, pady=16)

        # LEFT COLUMN
        left_col = ctk.CTkFrame(main_container, fg_color="transparent")
        left_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))

        ctk.CTkLabel(left_col, text="GENERAL & PROCESSING", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(pady=(0, 8), anchor="w")
        
        # Path
        path_frame = ctk.CTkFrame(left_col, fg_color=C["surface"], corner_radius=8,
                                  border_width=1, border_color=C["border"])
        path_frame.pack(fill=tk.X, pady=(0, 8))
        ctk.CTkLabel(path_frame, text="Download Location", font=self.font_small,
                     text_color=C["text_dim"]).pack(anchor="w", padx=12, pady=(8, 2))
        path_inner = ctk.CTkFrame(path_frame, fg_color="transparent")
        path_inner.pack(fill=tk.X, padx=12, pady=(0, 8))
        path_var = tk.StringVar(value=self.download_folder)
        ctk.CTkEntry(path_inner, textvariable=path_var, state="readonly", font=self.font_small,
                     fg_color=C["surface_hover"], border_color=C["border"]).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))
        
        def change_folder():
            folder = filedialog.askdirectory(parent=settings_win, title="Select Download Folder", initialdir=self.download_folder)
            if folder:
                self.download_folder = folder
                self.save_setting("download_folder", folder)
                path_var.set(folder)
                self.location_label.configure(text=f"📂  {self.download_folder}")
        ctk.CTkButton(path_inner, text="Browse", width=70, command=change_folder,
                      font=self.font_bold, corner_radius=6, height=28,
                      fg_color=C["accent"], hover_color=C["accent_hover"]).pack(side=tk.LEFT)

        # Switches
        proc_frame = ctk.CTkFrame(left_col, corner_radius=10, fg_color=C["surface"],
                                  border_width=1, border_color=C["border"])
        proc_frame.pack(fill=tk.BOTH, expand=True, pady=4)
        
        switches = [
            ("Embed Metadata & Covers", self.embed_metadata, True),
            ("Download Subtitles", self.download_subs, True),
            ("Auto Shutdown PC", self.shutdown_pc, True),
            ("Speed Boost (Multi-thread)", self.use_aria2, True),
            ("Minimize to Tray", self.minimize_to_tray, TRAY_AVAILABLE),
            ("Desktop Notifications", self.notifications_enabled, NOTIFICATIONS_AVAILABLE),
        ]
        for text, var, available in switches:
            sw = ctk.CTkSwitch(proc_frame, text=text, variable=var, font=self.font_main,
                               progress_color=C["accent"], button_color=C["text_dim"],
                               button_hover_color=C["text"], fg_color=C["border"])
            sw.pack(pady=6, anchor="w", padx=14)
            if not available:
                sw.configure(state="disabled", text=f"{text} (unavailable)")

        # Remote + Update row
        bottom_row = ctk.CTkFrame(left_col, fg_color="transparent")
        bottom_row.pack(fill=tk.X, pady=(8, 0))
        
        remote_btn = ctk.CTkButton(bottom_row, text="📱  Remote Control", command=self.show_remote_qr,
                                   font=self.font_bold, height=34, corner_radius=8,
                                   fg_color=C["purple"], hover_color=self._darken(C["purple"]))
        remote_btn.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 4))
        if not SERVER_AVAILABLE:
            remote_btn.configure(state="disabled", text="📱  Remote (unavailable)")

        btn_update = ctk.CTkButton(bottom_row, text="↻  Update Engine",
                                   command=lambda: self.update_yt_dlp(btn_update), 
                                   font=self.font_bold, height=34, corner_radius=8,
                                   fg_color="transparent", border_width=1,
                                   border_color=C["border"], text_color=C["text_dim"],
                                   hover_color=C["surface_hover"])
        btn_update.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 0))

        # RIGHT COLUMN
        right_col = ctk.CTkFrame(main_container, fg_color="transparent")
        right_col.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))

        ctk.CTkLabel(right_col, text="NETWORK & OPTIMIZATION", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(pady=(0, 8), anchor="w")
        
        net_frame = ctk.CTkFrame(right_col, corner_radius=10, fg_color=C["surface"],
                                 border_width=1, border_color=C["border"])
        net_frame.pack(fill=tk.BOTH, expand=True)

        net_items = [
            ("Simultaneous Downloads", 0),
            ("Max Speed (MB/s)", 1),
            ("Browser Cookies", 2),
            ("Proxy (SOCKS5/HTTP)", 3),
        ]
        for label_text, row in net_items:
            ctk.CTkLabel(net_frame, text=label_text, font=self.font_main,
                         text_color=C["text"]).grid(row=row, column=0, padx=14, pady=12, sticky="e")
        
        concurrent_combo = ctk.CTkComboBox(net_frame, values=["1", "2", "3", "4", "5"], width=80,
                                           command=lambda v: self.concurrent_downloads.set(v),
                                           font=self.font_main, dropdown_font=self.font_main,
                                           fg_color=C["surface_hover"], border_color=C["border"],
                                           button_color=C["accent"], button_hover_color=C["accent_hover"])
        concurrent_combo.set(self.concurrent_downloads.get())
        concurrent_combo.grid(row=0, column=1, sticky="w", pady=12)
        
        speed_entry = ctk.CTkEntry(net_frame, width=80, font=self.font_main,
                                   fg_color=C["surface_hover"], border_color=C["border"],
                                   text_color=C["text"])
        speed_entry.insert(0, self.speed_limit.get())
        speed_entry.grid(row=1, column=1, sticky="w", pady=12)
        speed_entry.bind("<KeyRelease>", lambda e: self.speed_limit.set(speed_entry.get()))
        
        browser_combo = ctk.CTkComboBox(net_frame, values=["None", "chrome", "edge", "firefox", "brave", "opera"],
                                        width=110, command=lambda v: self.browser_cookie.set(v),
                                        font=self.font_main, dropdown_font=self.font_main,
                                        fg_color=C["surface_hover"], border_color=C["border"],
                                        button_color=C["accent"], button_hover_color=C["accent_hover"])
        browser_combo.set(self.browser_cookie.get())
        browser_combo.grid(row=2, column=1, sticky="w", pady=12)

        proxy_entry = ctk.CTkEntry(net_frame, width=180, font=self.font_main,
                                   placeholder_text="socks5://127.0.0.1:1080",
                                   fg_color=C["surface_hover"], border_color=C["border"],
                                   text_color=C["text"], placeholder_text_color=C["text_dim"])
        proxy_entry.insert(0, self.proxy_url.get())
        proxy_entry.grid(row=3, column=1, sticky="w", pady=12)
        proxy_entry.bind("<KeyRelease>", lambda e: self.proxy_url.set(proxy_entry.get()))

    def add_single(self):
        C = self.COLORS
        clipboard_text = ""
        try:
            clip = self.root.clipboard_get()
            if "http" in clip: clipboard_text = clip.strip()
        except tk.TclError: pass 

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add New Download")
        dialog.configure(fg_color=C["bg"])
        self.center_toplevel(dialog, 540, 400)
        dialog.transient(self.root)
        dialog.grab_set()

        # Header
        header = ctk.CTkFrame(dialog, fg_color=C["surface"], corner_radius=0)
        header.pack(fill=tk.X)
        ctk.CTkLabel(header, text="+ New Download", font=self.font_title,
                     text_color=C["text"]).pack(pady=14, padx=20, anchor="w")
        
        main_frame = ctk.CTkFrame(dialog, corner_radius=12, fg_color=C["surface"],
                                  border_width=1, border_color=C["border"])
        main_frame.pack(padx=20, fill=tk.BOTH, expand=True, pady=16)

        ctk.CTkLabel(main_frame, text="URL", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(pady=(14, 4), padx=18, anchor="w")
        url_entry = ctk.CTkEntry(main_frame, font=self.font_main, height=40,
                                 placeholder_text="https://www.youtube.com/watch?v=...",
                                 fg_color=C["surface_hover"], border_color=C["border"],
                                 text_color=C["text"], placeholder_text_color=C["text_dim"])
        url_entry.pack(pady=(0, 12), padx=18, fill=tk.X)
        if clipboard_text: url_entry.insert(0, clipboard_text)

        ctk.CTkLabel(main_frame, text="TIME RANGE (OPTIONAL)", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(pady=(4, 4), padx=18, anchor="w")
        time_inner = ctk.CTkFrame(main_frame, fg_color="transparent")
        time_inner.pack(pady=(0, 14), padx=18, fill=tk.X)
        
        ctk.CTkLabel(time_inner, text="Start", font=self.font_small,
                     text_color=C["text_dim"]).grid(row=0, column=0, padx=(0, 6))
        start_entry = ctk.CTkEntry(time_inner, width=90, placeholder_text="00:00",
                                   font=self.font_main, height=34,
                                   fg_color=C["surface_hover"], border_color=C["border"],
                                   text_color=C["text"])
        start_entry.grid(row=0, column=1, padx=(0, 16))
        
        ctk.CTkLabel(time_inner, text="End", font=self.font_small,
                     text_color=C["text_dim"]).grid(row=0, column=2, padx=(0, 6))
        end_entry = ctk.CTkEntry(time_inner, width=90, placeholder_text="End",
                                 font=self.font_main, height=34,
                                 fg_color=C["surface_hover"], border_color=C["border"],
                                 text_color=C["text"])
        end_entry.grid(row=0, column=3)
            
        def submit():
            val = url_entry.get().strip()
            start_val = start_entry.get().strip()
            end_val = end_entry.get().strip()
            
            range_str = "Full Video"
            if start_val or end_val:
                s = start_val if start_val else "00:00"
                e = end_val if end_val else "End"
                range_str = f"{s} to {e}"
                
            dialog.destroy()
            if val: self.fetch_and_add(val, range_str)

        def trim_submit():
            val = url_entry.get().strip()
            if not val:
                messagebox.showwarning("Empty URL", "Please paste a URL first.")
                return
            
            # Check for FFmpeg first
            if not get_ffmpeg_path():
                messagebox.showerror("FFmpeg Missing", "Visual Trimming requires FFmpeg.")
                return

            dialog.destroy()
            self.status_label.configure(text="Fetching media for trimmer...")
            
            def fetch_and_launch():
                ydl_opts = {'quiet': True, 'extract_flat': True, 'no_warnings': True}
                self._apply_cookies(ydl_opts)
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(val, download=False)
                        duration = info.get('duration')
                        title = info.get('title', 'Unknown Title')
                        if duration:
                            def launch():
                                MediaTrimmerDialog(self.root, title, duration, 
                                                  lambda r: self.fetch_and_add(val, r))
                            self.root.after(0, launch)
                            self.root.after(0, lambda: self.status_label.configure(text="Ready"))
                        else:
                            self.root.after(0, lambda: messagebox.showerror("Error", "Could not fetch media duration."))
                except Exception as e:
                    self.root.after(0, lambda: messagebox.showerror("Error", f"Failed to fetch metadata: {str(e)}"))

            threading.Thread(target=fetch_and_launch, daemon=True).start()

        btn_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        btn_frame.pack(pady=(0, 20), fill=tk.X, padx=20)

        ctk.CTkButton(btn_frame, text="Add to Queue", command=submit, font=self.font_bold,
                      height=42, corner_radius=8, fg_color=C["accent"],
                      hover_color=C["accent_hover"]).pack(side=tk.LEFT, padx=(0, 8), fill=tk.X, expand=True)
        ctk.CTkButton(btn_frame, text="Visual Trim & Add", command=trim_submit,
                      font=self.font_bold, height=42, corner_radius=8,
                      fg_color=C["purple"], hover_color=self._darken(C["purple"])).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def add_channel(self):
        C = self.COLORS
        clipboard_text = ""
        try:
            clip = self.root.clipboard_get()
            if "http" in clip: clipboard_text = clip.strip()
        except tk.TclError: pass 

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Channel & Playlist Crawler")
        dialog.configure(fg_color=C["bg"])
        self.center_toplevel(dialog, 540, 330)
        dialog.transient(self.root)
        dialog.grab_set()

        # Header
        header = ctk.CTkFrame(dialog, fg_color=C["surface"], corner_radius=0)
        header.pack(fill=tk.X)
        ctk.CTkLabel(header, text="\U0001f3ac  Channel / Playlist Crawler", font=self.font_title,
                     text_color=C["text"]).pack(pady=14, padx=20, anchor="w")
        
        main_frame = ctk.CTkFrame(dialog, corner_radius=12, fg_color=C["surface"],
                                  border_width=1, border_color=C["border"])
        main_frame.pack(padx=20, fill=tk.BOTH, expand=True, pady=16)

        ctk.CTkLabel(main_frame, text="CHANNEL OR PLAYLIST URL", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(pady=(14, 4), padx=18, anchor="w")
        url_entry = ctk.CTkEntry(main_frame, font=self.font_main, height=40,
                                 placeholder_text="https://www.youtube.com/@channel/videos",
                                 fg_color=C["surface_hover"], border_color=C["border"],
                                 text_color=C["text"], placeholder_text_color=C["text_dim"])
        url_entry.pack(pady=(0, 14), padx=18, fill=tk.X)
        if clipboard_text: url_entry.insert(0, clipboard_text)

        def submit():
            val = url_entry.get().strip()
            if "youtube.com/@" in val and "/" not in val.split("@")[1]:
                val = val.rstrip("/") + "/videos"
                
            dialog.destroy()
            if val: 
                crawler = PlaylistCrawlerDialog(self.root, lambda sel: self.add_entries_to_ui(sel, "Full Video"))
                self.fetch_and_add(val, "Full Video", crawler)
                
        ctk.CTkButton(dialog, text="Start Crawling", command=submit, font=self.font_bold,
                      height=42, corner_radius=8, fg_color=C["purple"],
                      hover_color=self._darken(C["purple"]),
                      width=200).pack(pady=(0, 20))

    def add_batch(self):
        C = self.COLORS
        batch_win = ctk.CTkToplevel(self.root)
        batch_win.title("Batch Add Links")
        batch_win.configure(fg_color=C["bg"])
        self.center_toplevel(batch_win, 520, 500)
        batch_win.transient(self.root)
        batch_win.grab_set()
        
        # Header
        header = ctk.CTkFrame(batch_win, fg_color=C["surface"], corner_radius=0)
        header.pack(fill=tk.X)
        ctk.CTkLabel(header, text="\U0001f4c2  Batch Import Links", font=self.font_title,
                     text_color=C["text"]).pack(pady=14, padx=20, anchor="w")
        
        main_frame = ctk.CTkFrame(batch_win, corner_radius=12, fg_color=C["surface"],
                                  border_width=1, border_color=C["border"])
        main_frame.pack(padx=20, fill=tk.BOTH, expand=True, pady=16)

        ctk.CTkLabel(main_frame, text="PASTE ONE URL PER LINE", font=self.font_tiny,
                     text_color=C["text_dim"]).pack(pady=(14, 6), padx=18, anchor="w")
        text_box = ctk.CTkTextbox(main_frame, height=220, font=self.font_main,
                                  border_width=1, corner_radius=8,
                                  fg_color=C["surface_hover"], border_color=C["border"],
                                  text_color=C["text"])
        text_box.pack(pady=(0, 14), padx=18, fill=tk.BOTH, expand=True)
        
        def process_batch():
            urls = text_box.get("1.0", tk.END).strip().split('\n')
            batch_win.destroy()
            for url in urls:
                if url.strip(): self.fetch_and_add(url.strip(), "Full Video")
                    
        ctk.CTkButton(batch_win, text="Add All to Queue", command=process_batch,
                      font=self.font_bold, height=42, corner_radius=8,
                      fg_color=C["success"], hover_color=self._darken(C["success"]),
                      width=200).pack(pady=(0, 20))

    def remove_selected(self):
        with sqlite3.connect(self.db_path) as conn:
            for item in self.tree.selection():
                db_id = int(item.replace("db_", ""))
                conn.execute('DELETE FROM downloads WHERE db_id=?', (db_id,))
                self.tree.delete(item)
            conn.commit()

    def clear_all(self):
        if messagebox.askyesno("Clear Queue", "Are you sure you want to remove all items from the list?"):
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('DELETE FROM downloads')
                conn.commit()
            for item in self.tree.get_children(): self.tree.delete(item)

    # --- Core Downloader Logic ---
    def fetch_and_add(self, url, range_str, crawler_dialog=None):
        self.status_label.configure(text="Fetching info...")
        # If it's a single video, we can afford full extraction for size
        is_single = "playlist" not in url.lower() and "channel" not in url.lower() and "/user/" not in url.lower()
        threading.Thread(target=self._fetch_thread, args=(url, range_str, crawler_dialog, is_single), daemon=True).start()

    def _fetch_thread(self, url, range_str, crawler_dialog=None, full_extract=False):
        ydl_opts = {'extract_flat': not full_extract, 'quiet': not crawler_dialog, 'no_warnings': True}
        self._apply_cookies(ydl_opts)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
            if info.get('_type') == 'playlist':
                playlist_title = info.get('title', 'Unknown Playlist')
                entries = info.get('entries', [])
                
                # Channel Detection: If we only find sub-playlists (tabs like Videos, Shorts)
                # we should try to extract the videos from them instead.
                tabs = [e for e in entries if e.get('_type') == 'url' and e.get('title') in ["Videos", "Shorts", "Live"]]
                if tabs:
                    self.status_label.configure(text="Expanding channel tabs...")
                    if crawler_dialog: self.root.after(0, lambda: crawler_dialog.title("Expanding Channel..."))
                    
                    expanded_entries = []
                    # Prioritize 'Videos' tab, then add others if needed
                    for tab in tabs[:2]:
                        try:
                            with yt_dlp.YoutubeDL(ydl_opts) as tab_ydl:
                                tab_info = tab_ydl.extract_info(tab['url'], download=False)
                                expanded_entries.extend(tab_info.get('entries', []))
                        except: continue
                    if expanded_entries: entries = expanded_entries

                if crawler_dialog:
                    self.root.after(0, lambda: crawler_dialog.show_entries(playlist_title, entries))
                else:
                    self.root.after(0, lambda: PlaylistCrawlerDialog(self.root, 
                                                                    lambda selected: self.add_entries_to_ui(selected, range_str),
                                                                    playlist_title, entries))
                self.root.after(0, lambda: self.status_label.configure(text="Ready"))
            else:
                if crawler_dialog: self.root.after(0, crawler_dialog.destroy)
                info['size_str'] = self.estimate_size(info, self.get_current_media_type_str())
                self.add_entries_to_ui([info], range_str)
                
        except Exception as e:
            if crawler_dialog: self.root.after(0, crawler_dialog.destroy)
            self.root.after(0, lambda: self.status_label.configure(text="Error fetching link (Try linking browser cookies)"))

    def add_entries_to_ui(self, entries, range_str):
        if self.media_type.get() == "Audio":
            current_format = f"Audio ({self.format_var.get()} - {self.bitrate_var.get()}k)"
        elif self.media_type.get() == "Video":
            current_format = f"Video ({self.format_var.get()})"
        else:
            current_format = f"Thumb ({self.format_var.get()})"
            
        new_item_ids = []
        for entry in entries:
            title = entry.get('title', 'Unknown Title')
            vid_url = entry.get('webpage_url') or entry.get('original_url') or entry.get('url')
            if not vid_url and entry.get('id'): vid_url = f"https://www.youtube.com/watch?v={entry['id']}"
            
            if vid_url:
                idx = len(self.tree.get_children()) + 1
                # Estimate size if we have metadata
                size_str = entry.get('size_str', '---')
                
                # Save to DB first to get an ID
                db_values = ("☑", title, vid_url, range_str, current_format, "Queued", "", size_str)
                db_id = self.update_db_item(db_values)
                
                # Cache metadata for live updates
                self.metadata_cache[db_id] = entry
                
                # Insert into the tree
                item_id = self.tree.insert("", "end", iid=f"db_{db_id}", 
                                         values=("☑", idx, title, vid_url, range_str, current_format, size_str, "Queued"))
                new_item_ids.append(item_id)
                
        if new_item_ids: 
            self.tree.selection_set(new_item_ids)
            self.tree.see(new_item_ids[-1]) # Auto-scroll to newly added
        self.root.after(0, lambda: self.status_label.configure(text="Ready"))

    def get_current_media_type_str(self):
        if self.media_type.get() == "Audio":
            return f"Audio ({self.format_var.get()} - {self.bitrate_var.get()}k)"
        elif self.media_type.get() == "Video":
            return f"Video ({self.format_var.get()})"
        else:
            return f"Thumb ({self.format_var.get()})"

    def estimate_size(self, info, media_type):
        """Attempts to estimate file size based on selected media_type."""
        if not info or 'formats' not in info:
            return "---"
            
        formats = info.get('formats', [])
        if not formats: return "---"
        
        try:
            # Audio Mode
            if "Audio" in media_type:
                audio_formats = [f for f in formats if f.get('vcodec') == 'none' or f.get('acodec') != 'none']
                if not audio_formats: return "---"
                
                # Try to find a format with filesize
                with_size = [f for f in audio_formats if f.get('filesize') or f.get('filesize_approx')]
                if not with_size: return "---"
                
                best_audio = max(with_size, key=lambda f: f.get('abr', 0) or 0)
                size = best_audio.get('filesize') or best_audio.get('filesize_approx')
                return self.format_size_simple(size)
                
            # Video Mode
            elif "Video" in media_type:
                res_str = re.search(r'\((.*?)\)', media_type).group(1) 
                res_val = int(res_str.replace("p", ""))
                
                # Find best video format for this resolution
                video_formats = [f for f in formats if f.get('height') == res_val]
                if not video_formats:
                    video_formats = [f for f in formats if f.get('height') and f.get('height') <= res_val]
                
                if not video_formats: return "---"
                
                # Sort by height then bitrate
                video_formats.sort(key=lambda f: (f.get('height', 0), f.get('tbr', 0)), reverse=True)
                best_video = video_formats[0]
                
                v_size = best_video.get('filesize') or best_video.get('filesize_approx')
                
                # Find an audio format to add to it if it's video-only
                if best_video.get('vcodec') != 'none' and best_video.get('acodec') == 'none':
                    audio_formats = [f for f in formats if f.get('vcodec') == 'none']
                    if audio_formats:
                        best_audio = max(audio_formats, key=lambda f: f.get('abr', 0) or 0)
                        a_size = best_audio.get('filesize') or best_audio.get('filesize_approx')
                        if v_size and a_size:
                            return self.format_size_simple(v_size + a_size)
                
                return self.format_size_simple(v_size) if v_size else "---"
        except:
            return "---"
            
        return "---"

    # --- Download Control Logic ---
    def resume_selected(self):
        selection = self.tree.selection()
        if not selection:
            # Fallback to selected marked items if no Treeview selection
            all_items = self.tree.get_children()
            selection = [item for item in all_items if self.tree.item(item, "values")[0] == "☑"]
            
        if not selection:
            messagebox.showwarning("Warning", "No items selected to resume.", parent=self.root)
            return

        for item_id in selection:
            self.active_downloads[item_id] = False # Reset cancel flag
            current_values = list(self.tree.item(item_id, "values"))
            if current_values[7] != "Done":
                current_values[7] = "Queued"
                self.tree.item(item_id, values=current_values)
                db_id = int(item_id.replace("db_", ""))
                with sqlite3.connect(self.db_path) as conn:
                    conn.execute('UPDATE downloads SET status="Queued" WHERE db_id=?', (db_id,))
                    conn.commit()

        self.stop_all_flag = False
        self._start_download_batch(selection)

    def pause_selected(self):
        selection = self.tree.selection()
        if not selection:
            all_items = self.tree.get_children()
            selection = [item for item in all_items if self.tree.item(item, "values")[0] == "☑"]
            
        if not selection: return

        count = 0
        for item_id in selection:
            if item_id in self.active_downloads:
                self.active_downloads[item_id] = True
                count += 1
                self.tree.set(item_id, "Status", "Pausing...")
        
        if count > 0:
            self.status_label.configure(text=f"Requested pause for {count} downloads.")
            self.send_notification("Downloads Paused", f"Paused {count} active download(s).")

    def stop_all(self):
        self.stop_all_flag = True
        for item_id in self.active_downloads:
            self.active_downloads[item_id] = True
        self.status_label.configure(text="Stopping all active downloads...")

    def download_selected(self):
        all_items = self.tree.get_children()
        selected_items = [item for item in all_items if self.tree.item(item, "values")[0] == "☑"]
        
        if not selected_items:
            messagebox.showwarning("Warning", "No items selected for download.", parent=self.root)
            return
            
        self.stop_all_flag = False
        self._start_download_batch(selected_items)

    def download_all(self):
        all_items = self.tree.get_children()
        if not all_items:
            messagebox.showwarning("Warning", "The queue is empty.", parent=self.root)
            return
            
        self.stop_all_flag = False
        self._start_download_batch(all_items)

    def _start_download_batch(self, items):
        self.status_label.configure(text="Processing Batch...")
        self.progress_bar.set(0)
        self.completed_items = 0
        self.total_items = len(items)
        
        # Start the thread pool executor in the background
        threading.Thread(target=self._run_thread_pool, args=(items,), daemon=True).start()

    def _run_thread_pool(self, items):
        max_workers = int(self.concurrent_downloads.get())
        
        # NEW: Concurrent execution using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._download_single_item, item) for item in items]
            concurrent.futures.wait(futures)
            
        # When all threads are completely finished
        if self.stop_all_flag:
            msg = "Batch Stopped"
            detail = "The download batch was terminated by user."
            status_text = "Stopped"
            color = "#EF4444"
        else:
            msg = "All Downloads Complete"
            detail = f"Successfully processed {self.total_items} item(s)."
            status_text = "All downloads in queue complete!"
            color = "#34D399"

        self.root.after(0, lambda: self.status_label.configure(text=status_text, text_color=color))
        self.root.after(0, lambda: self.progress_bar.set(1)) 
        self.root.after(0, lambda: self.send_notification(msg, detail))

        if self.shutdown_pc.get() and not self.stop_all_flag:
            os.system("shutdown /s /t 10") 

    def _download_single_item(self, item):
        values = self.tree.item(item, "values")
        # Indices update due to Selection column at index 0
        sel, idx, title, url, time_range, media_type, file_size, status = values
        
        if status == "Done" or status == "Pausing...":
            self._increment_global_progress()
            return 
            
        self.root.after(0, lambda i=item: self.tree.set(i, "Status", "Starting..."))
        
        # Track as active
        self.active_downloads[item] = False
        db_id = int(item.replace("db_", ""))
        safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
        
        # --- Base Options ---
        ffmpeg_exe = get_ffmpeg_path()
        ffmpeg_dir = os.path.dirname(ffmpeg_exe) if ffmpeg_exe and os.path.isabs(ffmpeg_exe) else None

        ydl_opts = {
            'quiet': True,
            'no_warnings': True,
            'logger': YTDLLogger(self, item),
            'ffmpeg_location': ffmpeg_dir if ffmpeg_dir else None,
            'ignoreerrors': True,
            'progress_hooks': [self.create_per_item_hook(item)],
            'concurrent_fragment_downloads': 5,
            'prefer_ffmpeg': True,
            'postprocessor_args': {
                'ffmpeg': ['-threads', '0']
            },
        }
        
        if "Video" in media_type:
            ydl_opts['postprocessor_args']['ffmpeg'].extend(['-preset', self.PRESET_MAP.get(self.ffmpeg_preset.get(), 'medium')])

        # Extensions and paths logic...
        ext_choice = "mp4"
        if "Thumb" in media_type:
            ext_choice = re.search(r'\((.*?)\)', media_type).group(1)
        elif "Audio" in media_type:
            if "wav" in media_type: ext_choice = "wav"
            elif "m4a" in media_type: ext_choice = "m4a"
            else: ext_choice = "mp3"
        
        final_file_path = ""
        if time_range != "Full Video":
            try:
                if time_range.startswith("*"): # Visual Trimmer Range: *HH:MM:SS-HH:MM:SS
                    range_clean = time_range[1:]
                    start_str, end_str = range_clean.split("-")
                else: # Manual Range: HH:MM:SS to HH:MM:SS
                    start_str, end_str = time_range.split(" to ")
                
                start_sec = parse_time(start_str)
                end_sec = parse_time(end_str)
                s = start_sec if start_sec is not None else 0
                e = end_sec if end_sec is not None else float('inf')
                
                # Standard yt-dlp range logic requires ffmpeg_location to be a directory
                ydl_opts['download_ranges'] = download_range_func(None, [(s, e)])
                ydl_opts['force_keyframes_at_cuts'] = True
                
                # Update UI to indicate progress is blind during trimming
                self.root.after(0, lambda i=item: self.tree.set(i, "Status", "Trimming (No Progress Bar)..."))

                # Ensure external downloader args are clean
                if 'external_downloader' in ydl_opts:
                    del ydl_opts['external_downloader']
            except Exception as ex:
                print(f"Error parsing range {time_range}: {ex}")

        self._apply_cookies(ydl_opts)
        if self.speed_limit.get().isdigit() and int(self.speed_limit.get()) > 0:
            ydl_opts['ratelimit'] = int(self.speed_limit.get()) * 1024 * 1024 
        
        # --- NEW: Native Aggressive Concurrency ---
        if self.use_aria2.get():
            ydl_opts['concurrent_fragment_downloads'] = 16
        else:
            ydl_opts['concurrent_fragment_downloads'] = 5 # Default
            
        # --- NEW: Proxy Support ---
        if self.proxy_url.get().strip():
            ydl_opts['proxy'] = self.proxy_url.get().strip()

        if self.download_subs.get() and "Thumb" not in media_type:
            ydl_opts['writesubtitles'] = True
            ydl_opts['writeautomaticsub'] = True
            ydl_opts['subtitleslangs'] = ['en']

        if "Thumb" in media_type:
            final_file_path = os.path.join(self.download_folder, f'{safe_title}.{ext_choice}')
            ydl_opts['outtmpl'] = final_file_path
            ydl_opts['skip_download'] = True 
            ydl_opts['writethumbnail'] = True
            ydl_opts['postprocessors'] = [{'key': 'FFmpegThumbnailsConvertor', 'format': ext_choice, 'when': 'before_dl'}]
        elif "Audio" in media_type:
            final_file_path = os.path.join(self.download_folder, f'{safe_title}.{ext_choice}')
            ydl_opts['outtmpl'] = final_file_path
            bitrate = re.search(r'- (\d+)k', media_type).group(1)
            ydl_opts['format'] = 'bestaudio/best'
            ydl_opts['postprocessors'] = [{
                'key': 'FFmpegExtractAudio',
                'preferredcodec': ext_choice,
                'preferredquality': bitrate,
            }]
            if self.embed_metadata.get():
                ydl_opts['writethumbnail'] = True
                ydl_opts['postprocessors'].extend([{'key': 'EmbedThumbnail'}, {'key': 'FFmpegMetadata'}])
        else: 
            res = re.search(r'\((.*?)\)', media_type).group(1) 
            final_file_path = os.path.join(self.download_folder, f'{safe_title}_{res}.{ext_choice}')
            ydl_opts['outtmpl'] = final_file_path
            
            if res.lower() == "best quality":
                ydl_opts['format'] = 'bestvideo+bestaudio/best'
            else:
                res_map = {"4K": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360, "240p": 240}
                target_height = 1080
                for key, val in res_map.items():
                    if key in media_type:
                        target_height = val
                        break
                # Video format selection: prioritize best quality up to target height, allowing modern codecs
                # Fallback 1: Pre-muxed video up to target height
                # Fallback 2: Best video + best audio (ignores height filter, prevents failing on vertical videos)
                # Fallback 3: Best pre-muxed video (ignores height filter)
                # Fallback 4: Any video / any audio
                ydl_opts['format'] = f'bestvideo[height<={target_height}]+bestaudio/best[height<={target_height}]/bestvideo+bestaudio/best/bestvideo/bestaudio'
                
            ydl_opts['merge_output_format'] = 'mp4'
            if self.embed_metadata.get():
                ydl_opts['writethumbnail'] = True
                ydl_opts['postprocessors'] = [{'key': 'EmbedThumbnail'}, {'key': 'FFmpegMetadata'}]

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                # Re-fetch info to get sizes for the specific selected quality
                info = ydl.extract_info(url, download=False)
                self.metadata_cache[db_id] = info # Update cache with full info
                size_str = self.estimate_size(info, media_type)
                self.root.after(0, lambda i=item, s=size_str: self.tree.set(i, "Size", s))
                
                info = ydl.extract_info(url, download=True)
                
                # Capture the ACTUAL final path from info dict
                if 'requested_downloads' in info and info['requested_downloads']:
                    # Prioritize the file that matches our ext_choice or is the largest
                    downloads = info['requested_downloads']
                    best_dl = downloads[0]
                    for dl in downloads:
                        if dl.get('ext') == ext_choice:
                            best_dl = dl
                            break
                    final_file_path = best_dl.get('filepath', final_file_path)
                elif '_filename' in info:
                    final_file_path = info['_filename']
                
                # Final check: if we got a webp but expected mp4/mp3, and video exists, use it
                if final_file_path.endswith(".webp") and not ext_choice.endswith("webp"):
                    base_p = final_file_path.rsplit(".", 1)[0]
                    if os.path.exists(base_p + "." + ext_choice):
                        final_file_path = base_p + "." + ext_choice

            # Check if we stopped mid-download
            if self.stop_all_flag or self.active_downloads.get(item, False):
                raise StopDownloadException()

            self.root.after(0, lambda i=item: self.tree.set(i, "Status", "Done"))
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('UPDATE downloads SET status="Done", file_path=? WHERE db_id=?', (final_file_path, db_id))
                conn.commit()
        except StopDownloadException:
            new_status = "Stopped" if self.stop_all_flag else "Paused"
            self.root.after(0, lambda i=item, s=new_status: self.tree.set(i, "Status", s))
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('UPDATE downloads SET status=? WHERE db_id=?', (new_status, db_id))
                conn.commit()
        except Exception:
            self.root.after(0, lambda i=item: self.tree.set(i, "Status", "Error"))
            with sqlite3.connect(self.db_path) as conn:
                conn.execute('UPDATE downloads SET status="Error" WHERE db_id=?', (db_id,))
                conn.commit()
        finally:
            if item in self.active_downloads:
                del self.active_downloads[item]
            if item in self.active_speeds:
                del self.active_speeds[item]
            
        self._increment_global_progress()

    def _increment_global_progress(self):
        with self.download_lock:
            self.completed_items += 1
            progress = self.completed_items / self.total_items
            self.root.after(0, lambda p=progress: self.progress_bar.set(p))
            self.root.after(0, lambda c=self.completed_items, t=self.total_items: self.status_label.configure(text=f"Completed {c} of {t} files"))

    def create_per_item_hook(self, item_id):
        def hook(d):
            # Check for cancellation
            if self.stop_all_flag or self.active_downloads.get(item_id, False):
                print(f"Hook: Cancel detected for {item_id}")
                raise StopDownloadException()

            if d['status'] == 'downloading':
                percent = d.get('_percent_str', '0%').strip()
                speed_str = d.get('_speed_str', 'N/A').strip()
                
                # Safely get native float values from yt-dlp
                raw_speed = d.get('speed')
                if raw_speed is not None:
                    self.active_speeds[item_id] = float(raw_speed)
                    
                dl_bytes = d.get('downloaded_bytes')
                if dl_bytes is not None:
                    self.active_downloaded[item_id] = float(dl_bytes)

                # Update the specific row with its own live stats
                self.root.after(0, lambda i=item_id, p=percent, s=speed_str: self.tree.set(i, "Status", f"{p} ({s})"))
                
            elif d['status'] == 'finished':
                if item_id in self.active_speeds: del self.active_speeds[item_id]
                if item_id in self.active_downloaded:
                    self.session_downloaded_finished += self.active_downloaded.pop(item_id, 0)
                self.root.after(0, lambda i=item_id: self.tree.set(i, "Status", "Finalizing/Converting..."))
        return hook

    # --- Dashboard Methods ---
    def setup_dashboard_ui(self):
        C = self.COLORS
        dash_frame = ctk.CTkFrame(self.tab_stats, fg_color="transparent")
        dash_frame.pack(fill=tk.BOTH, expand=True, padx=16, pady=16)
        
        # Row 1: Key Metrics
        metrics_frame = ctk.CTkFrame(dash_frame, fg_color="transparent")
        metrics_frame.pack(fill=tk.X, pady=(0, 16))
        
        self.metric_cards = []
        metric_configs = [
            ("🕒  Current Speed", "0 KB/s",   C["accent"]),
            ("📈  Peak Speed",    "0 KB/s",   C["warning"]),
            ("📦  Downloaded",    "0 MB",     C["success"]),
            ("⏱️  Session Time",  "00:00:00", C["purple"])
        ]
        
        for i, (title, val, color) in enumerate(metric_configs):
            card = ctk.CTkFrame(metrics_frame, corner_radius=12, fg_color=C["surface"],
                                border_width=1, border_color=C["border"])
            card.grid(row=0, column=i, padx=6, sticky="nsew")
            metrics_frame.grid_columnconfigure(i, weight=1)
            
            # Colored accent bar at top
            accent_bar = ctk.CTkFrame(card, height=3, corner_radius=0, fg_color=color)
            accent_bar.pack(fill=tk.X, padx=20, pady=(12, 0))
            
            ctk.CTkLabel(card, text=title, font=self.font_small,
                         text_color=C["text_dim"]).pack(pady=(8, 2))
            label_val = ctk.CTkLabel(card, text=val, font=("Segoe UI", 22, "bold"),
                                     text_color=C["text"])
            label_val.pack(pady=(0, 14))
            self.metric_cards.append(label_val)

        # Row 2: Speed Graph
        graph_container = ctk.CTkFrame(dash_frame, corner_radius=12, fg_color=C["surface"],
                                       border_width=1, border_color=C["border"])
        graph_container.pack(fill=tk.BOTH, expand=True, pady=8)
        
        ctk.CTkLabel(graph_container, text="Real-time Bandwidth  (60s window)",
                     font=self.font_heading, text_color=C["text_dim"]).pack(pady=(12, 4))
        
        self.speed_canvas = tk.Canvas(graph_container, bg=C["bg"], highlightthickness=0)
        self.speed_canvas.pack(fill=tk.BOTH, expand=True, padx=16, pady=(0, 16))
        
        # Row 3: System Stats
        bottom_box = ctk.CTkFrame(dash_frame, fg_color="transparent")
        bottom_box.pack(fill=tk.X, pady=(8, 0))
        
        # Disk Usage
        self.disk_frame = ctk.CTkFrame(bottom_box, corner_radius=12, fg_color=C["surface"],
                                       border_width=1, border_color=C["border"])
        self.disk_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 6))
        
        ctk.CTkLabel(self.disk_frame, text="💽  Disk Health", font=self.font_heading,
                     text_color=C["text_dim"]).pack(pady=(14, 4))
        self.disk_info_label = ctk.CTkLabel(self.disk_frame, text="Scanning…",
                                            font=self.font_small, text_color=C["text"])
        self.disk_info_label.pack()
        
        self.disk_progress = ctk.CTkProgressBar(self.disk_frame, height=6, corner_radius=3,
                                                progress_color=C["success"],
                                                fg_color=C["surface_hover"])
        self.disk_progress.pack(fill=tk.X, padx=28, pady=(8, 14))

        # System Load
        self.sys_frame = ctk.CTkFrame(bottom_box, corner_radius=12, fg_color=C["surface"],
                                      border_width=1, border_color=C["border"])
        self.sys_frame.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(6, 0))
        
        ctk.CTkLabel(self.sys_frame, text="💻  System Load", font=self.font_heading,
                     text_color=C["text_dim"]).pack(pady=(14, 4))
        self.sys_info_label = ctk.CTkLabel(self.sys_frame, text="Scanning…",
                                           font=self.font_small, text_color=C["text"])
        self.sys_info_label.pack(pady=(0, 14))



    def update_dashboard_stats(self):
        if not self.root.winfo_exists(): return
        
        try:
            # 1. Update Speed History
            # Use list() to avoid "dictionary changed size during iteration"
            current_active_speeds = list(self.active_speeds.values())
            self.current_speed_avg = sum(current_active_speeds)
            
            if self.current_speed_avg > self.peak_speed:
                self.peak_speed = self.current_speed_avg

            self.speed_history.pop(0)
            self.speed_history.append(self.current_speed_avg)
            
            # 2. Update Metric Labels
            # Current Speed
            self.metric_cards[0].configure(text=self.format_bytes_per_sec(self.current_speed_avg))
            # Peak Speed
            self.metric_cards[1].configure(text=self.format_bytes_per_sec(self.peak_speed))
            # Total Downloaded (Live Calculation)
            total_active_dl = sum(list(self.active_downloaded.values()))
            total_data = self.session_downloaded_finished + total_active_dl
            self.metric_cards[2].configure(text=self.format_size_simple(total_data))
            # Session Time
            elapsed = int(time.time() - self.session_start_time)
            hrs, rem = divmod(elapsed, 3600)
            mins, secs = divmod(rem, 60)
            self.metric_cards[3].configure(text=f"{hrs:02d}:{mins:02d}:{secs:02d}")
            
            # 3. Draw Graph
            self.draw_speed_graph()
            
            # 4. Disk & System Info (Every few seconds)
            if int(time.time()) % 2 == 0:
                # Disk
                usage = psutil.disk_usage(self.download_folder)
                self.disk_info_label.configure(text=f"{self.format_size_simple(usage.free)} free space ({usage.percent}%)")
                self.disk_progress.set(usage.percent / 100)
                
                # System
                self.sys_info_label.configure(text=f"CPU: {psutil.cpu_percent()}% | RAM: {psutil.virtual_memory().percent}%")
        except Exception as e:
            print(f"Dashboard Update Error: {e}")

        self.root.after(1000, self.update_dashboard_stats)

    def draw_speed_graph(self):
        try:
            C = self.COLORS
            w = self.speed_canvas.winfo_width()
            h = self.speed_canvas.winfo_height()
            if w < 10 or h < 10: return
            
            self.speed_canvas.delete("all")
            
            max_speed = max(max(self.speed_history), 1024 * 1024)  # Min 1MB scale
            points = []
            for i, speed in enumerate(self.speed_history):
                x = (i / 59) * w
                y = h - (speed / max_speed) * (h - 20) - 10
                points.append((x, y))
                
            # Draw Area fill
            poly_points = [(0, h), *points, (w, h)]
            self.speed_canvas.create_polygon(poly_points, fill="#162044", stipple="gray25", outline="")
            
            # Draw Line
            self.speed_canvas.create_line(points, fill=C["accent"], width=2, smooth=True)
            
            # Grid lines
            for i in range(1, 4):
                y_grid = (h / 4) * i
                self.speed_canvas.create_line(0, y_grid, w, y_grid, fill=C["border"], dash=(4, 4))
        except Exception:
            pass
    # get_session_total_size replaced by self.active_downloaded dynamic tracking above

    def format_bytes_per_sec(self, b):
        if b < 1024: return f"{b:.1f} B/s"
        if b < 1024 * 1024: return f"{b/1024:.1f} KB/s"
        return f"{b/(1024*1024):.1f} MB/s"

    def format_size_simple(self, b):
        if b is None: return "---"
        if b < 1024 * 1024: return f"{b/1024:.1f} KB"
        if b < 1024 * 1024 * 1024: return f"{b/(1024*1024):.1f} MB"
        return f"{b/(1024*1024*1024):.1f} GB"

    # --- Tray & Notification Methods ---
    def on_minimize(self, event):
        """Handle standard minimize button click."""
        if event.widget == self.root and self.minimize_to_tray.get() and TRAY_AVAILABLE:
            if self.root.state() == "iconic":
                self.root.withdraw()
                self.show_tray_icon()

    def on_close(self):
        """Handle window close event: minimize to tray or exit."""
        if self.minimize_to_tray.get() and TRAY_AVAILABLE:
            self.root.withdraw()
            self.show_tray_icon()
        else:
            self.quit_app()

    def create_icon_image(self):
        """Dynamically generate an icon for the system tray."""
        image = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        # Gradient background circle
        draw.ellipse((4, 4, 60, 60), fill="#2563EB")
        # Download arrow
        draw.polygon([(32, 50), (14, 24), (50, 24)], fill="white")
        draw.rectangle((26, 12, 38, 24), fill="white")
        return image

    def show_tray_icon(self):
        """Create and display the system tray icon."""
        if not TRAY_AVAILABLE: return
        
        if self.tray_icon:
            self.tray_icon.visible = True
            return

        image = self.create_icon_image()
        menu = pystray.Menu(
            pystray.MenuItem('Show Media Downloader', self.show_window, default=True),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem('Exit Application', self.quit_app)
        )
        self.tray_icon = pystray.Icon("MediaDownloaderPro", image, "Media Downloader Pro", menu)
        # Run tray in a separate daemon thread
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def show_window(self, icon=None, item=None):
        """Restore the window from the tray."""
        if self.tray_icon:
            self.tray_icon.visible = False
        self.root.after(0, self.root.deiconify)
        self.root.after(0, self.root.lift)
        self.root.after(0, self.root.focus_force)

    def quit_app(self, icon=None, item=None):
        """Completely exit the application."""
        if self.tray_icon:
            self.tray_icon.stop()
        self.root.destroy()
        sys.exit(0)

    def send_notification(self, title, message):
        """Send a Windows desktop notification."""
        if self.notifications_enabled.get() and NOTIFICATIONS_AVAILABLE:
            try:
                toast = Notification(app_id="Media Downloader Pro",
                                   title=title,
                                   msg=message)
                toast.show()
            except Exception as e:
                print(f"Notification error: {e}")

if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    app = DownloadManagerApp(root)
    root.mainloop()
