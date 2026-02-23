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

ctk.set_appearance_mode("Dark")  
ctk.set_default_color_theme("blue")  

def get_ffmpeg_path():
    if hasattr(sys, '_MEIPASS'):
        base_dir = sys._MEIPASS
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    
    exe_path = os.path.join(base_dir, 'ffmpeg.exe')
    if os.path.exists(exe_path):
        return exe_path
    return base_dir

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

class DownloadManagerApp:
    def __init__(self, root):
        self.root = root
        self.root.title("Media Downloader Pro")
        self.root.geometry("1050x680") # Made wider to fit the new Range and Progress columns
        
        # Core Settings
        self.download_folder = os.path.join(os.path.expanduser("~"), "Downloads")
        self.embed_metadata = tk.BooleanVar(value=True) 
        self.download_subs = tk.BooleanVar(value=False)
        self.shutdown_pc = tk.BooleanVar(value=False)
        self.speed_limit = tk.StringVar(value="0") 
        self.browser_cookie = tk.StringVar(value="None")
        self.concurrent_downloads = tk.StringVar(value="1") # NEW: Concurrency setting
        
        self.queue = []
        self.download_lock = threading.Lock() # To safely update the global progress bar
        self.setup_ui()

    def setup_ui(self):
        # --- TOP BAR (Settings & Formats) ---
        top_frame = ctk.CTkFrame(self.root, corner_radius=10)
        top_frame.pack(side=tk.TOP, fill=tk.X, padx=10, pady=10)

        ctk.CTkLabel(top_frame, text="Type:", font=ctk.CTkFont(weight="bold")).pack(side=tk.LEFT, padx=(15, 5), pady=10)
        
        self.media_type = tk.StringVar(value="Audio")
        ctk.CTkRadioButton(top_frame, text="Audio", variable=self.media_type, value="Audio", command=self.update_format_options).pack(side=tk.LEFT, padx=5)
        ctk.CTkRadioButton(top_frame, text="Video", variable=self.media_type, value="Video", command=self.update_format_options).pack(side=tk.LEFT, padx=5)
        ctk.CTkRadioButton(top_frame, text="Thumbnail Only", variable=self.media_type, value="Thumbnail", command=self.update_format_options).pack(side=tk.LEFT, padx=5)

        ctk.CTkLabel(top_frame, text="Format:", font=ctk.CTkFont(weight="bold")).pack(side=tk.LEFT, padx=(15, 5))
        self.format_var = tk.StringVar()
        self.format_combo = ctk.CTkComboBox(top_frame, variable=self.format_var, state="readonly", width=100)
        self.format_combo.pack(side=tk.LEFT, padx=5)
        self.format_var.trace_add("write", self.auto_update_all)

        self.bitrate_label = ctk.CTkLabel(top_frame, text="Bitrate:", font=ctk.CTkFont(weight="bold"))
        self.bitrate_label.pack(side=tk.LEFT, padx=(10, 5))
        self.bitrate_var = tk.StringVar(value="192")
        self.bitrate_combo = ctk.CTkComboBox(top_frame, variable=self.bitrate_var, values=["320", "256", "192", "128"], state="readonly", width=70)
        self.bitrate_combo.pack(side=tk.LEFT, padx=5)
        self.bitrate_var.trace_add("write", self.auto_update_all)

        # --- LEFT SIDEBAR (Controls) ---
        sidebar = ctk.CTkFrame(self.root, width=180, corner_radius=10)
        sidebar.pack(side=tk.LEFT, fill=tk.Y, padx=(10, 0), pady=(0, 10))

        buttons = [
            ("Add Single Link", self.add_single),
            ("Batch Add Links", self.add_batch),
            ("Remove Selected", self.remove_selected),
            ("Clear Queue", self.clear_all),
            ("⚙ Power Settings", self.open_settings)
        ]

        for text, cmd in buttons:
            ctk.CTkButton(sidebar, text=text, command=cmd, fg_color="transparent", border_width=2, text_color=("gray10", "#DCE4EE")).pack(pady=10, padx=15, fill=tk.X)

        ctk.CTkButton(sidebar, text="DOWNLOAD ALL", command=self.download_all, fg_color="#28a745", hover_color="#218838", font=ctk.CTkFont(weight="bold")).pack(side=tk.BOTTOM, pady=20, padx=15, fill=tk.X, ipady=5)

        # --- CENTER LIST (Queue) ---
        list_frame = ctk.CTkFrame(self.root, corner_radius=10)
        list_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))

        style = ttk.Style()
        style.theme_use("default")
        style.configure("Treeview", background="#2b2b2b", foreground="white", rowheight=30, fieldbackground="#2b2b2b", borderwidth=0)
        style.map('Treeview', background=[('selected', '#1f538d')])
        style.configure("Treeview.Heading", background="#333333", foreground="white", relief="flat", font=('Arial', 10, 'bold'))

        # NEW: Added "Range" column for clipping
        columns = ("#", "Title", "URL", "Range", "Type", "Status")
        self.tree = ttk.Treeview(list_frame, columns=columns, show="headings", selectmode="extended")
        
        self.tree.heading("#", text="No.")
        self.tree.column("#", width=50, anchor="center")
        self.tree.heading("Title", text="Title")
        self.tree.column("Title", width=220)
        self.tree.heading("URL", text="URL")
        self.tree.column("URL", width=180)
        self.tree.heading("Range", text="Range")
        self.tree.column("Range", width=110, anchor="center")
        self.tree.heading("Type", text="Type & Quality")
        self.tree.column("Type", width=130, anchor="center")
        self.tree.heading("Status", text="Progress / Status")
        self.tree.column("Status", width=160, anchor="center")

        tree_scroll = ctk.CTkScrollbar(list_frame, command=self.tree.yview)
        self.tree.configure(yscroll=tree_scroll.set)
        
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(10, 0), pady=10)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 10), pady=10)

        self.tree.bind("<Delete>", lambda e: self.remove_selected())

        # --- BOTTOM BAR (Overall Dashboard) ---
        bottom_frame = ctk.CTkFrame(self.root, corner_radius=10)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X, padx=10, pady=(0, 10))

        self.progress_var = tk.DoubleVar()
        self.progress_bar = ctk.CTkProgressBar(bottom_frame, variable=self.progress_var)
        self.progress_bar.pack(fill=tk.X, padx=15, pady=(15, 5))
        self.progress_bar.set(0) 

        stats_frame = ctk.CTkFrame(bottom_frame, fg_color="transparent")
        stats_frame.pack(fill=tk.X, padx=15, pady=(0, 5))

        self.status_label = ctk.CTkLabel(stats_frame, text="Ready", font=ctk.CTkFont(weight="bold"))
        self.status_label.pack(side=tk.LEFT)

        self.location_label = ctk.CTkLabel(bottom_frame, text=f"Saving to: {self.download_folder}", text_color="gray")
        self.location_label.pack(anchor="w", padx=15, pady=(0, 10))

        self.update_format_options()

    def center_toplevel(self, window, width, height):
        self.root.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() // 2) - (width // 2)
        y = self.root.winfo_y() + (self.root.winfo_height() // 2) - (height // 2)
        window.geometry(f"{width}x{height}+{x}+{y}")

    # --- UI Logic Methods ---
    def update_format_options(self, event=None):
        if self.media_type.get() == "Audio":
            self.format_combo.configure(values=["mp3", "wav", "m4a"])
            self.format_combo.set("mp3")
            self.bitrate_label.pack(side=tk.LEFT, padx=(10, 5))
            self.bitrate_combo.pack(side=tk.LEFT, padx=5)
        elif self.media_type.get() == "Video":
            self.format_combo.configure(values=["4K", "1440p", "1080p", "720p", "480p", "360p", "240p"])
            self.format_combo.set("1080p")
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
            
        if self.media_type.get() == "Audio":
            new_media_type = f"Audio ({self.format_var.get()} - {self.bitrate_var.get()}k)"
        elif self.media_type.get() == "Video":
            new_media_type = f"Video ({self.format_var.get()})"
        else:
            new_media_type = f"Thumb ({self.format_var.get()})"
            
        updated_count = 0
        for item in all_items:
            current_status = self.tree.item(item, "values")[5] # Index 5 is Status now
            if "Downloading" not in current_status: # Safely ignore active items
                self.tree.set(item, "Type", new_media_type)
                self.tree.set(item, "Status", "Queued")
                updated_count += 1
                
        if updated_count > 0:
            self.status_label.configure(text=f"Auto-updated {updated_count} item(s) to {new_media_type}.")

    def open_settings(self):
        settings_win = ctk.CTkToplevel(self.root)
        settings_win.title("Power Settings")
        self.center_toplevel(settings_win, 480, 500)
        settings_win.transient(self.root)
        settings_win.grab_set() 
        
        ctk.CTkLabel(settings_win, text="Default Download Location:", font=ctk.CTkFont(weight="bold")).pack(pady=(15, 0))
        path_frame = ctk.CTkFrame(settings_win, fg_color="transparent")
        path_frame.pack(pady=5)
        path_var = tk.StringVar(value=self.download_folder)
        ctk.CTkEntry(path_frame, textvariable=path_var, state="readonly", width=300).pack(side=tk.LEFT, padx=5)
        
        def change_folder():
            folder = filedialog.askdirectory(parent=settings_win, title="Select Download Folder", initialdir=self.download_folder)
            if folder:
                self.download_folder = folder
                path_var.set(folder)
                self.location_label.configure(text=f"Saving to: {self.download_folder}")
        ctk.CTkButton(path_frame, text="Browse", width=60, command=change_folder).pack(side=tk.LEFT)
        
        ctk.CTkLabel(settings_win, text="Network & Concurrency:", font=ctk.CTkFont(weight="bold")).pack(pady=(15, 0))
        net_frame = ctk.CTkFrame(settings_win, fg_color="transparent")
        net_frame.pack()

        # NEW: Concurrent Downloads Dropdown
        ctk.CTkLabel(net_frame, text="Simultaneous Downloads:").grid(row=0, column=0, padx=5, pady=5, sticky="e")
        concurrent_combo = ctk.CTkComboBox(net_frame, values=["1", "2", "3", "4", "5"], width=70, command=lambda v: self.concurrent_downloads.set(v))
        concurrent_combo.set(self.concurrent_downloads.get())
        concurrent_combo.grid(row=0, column=1, sticky="w")
        
        ctk.CTkLabel(net_frame, text="Max Speed (MB/s):").grid(row=1, column=0, padx=5, pady=5, sticky="e")
        speed_entry = ctk.CTkEntry(net_frame, width=70)
        speed_entry.insert(0, self.speed_limit.get())
        speed_entry.grid(row=1, column=1, sticky="w")
        speed_entry.bind("<KeyRelease>", lambda e: self.speed_limit.set(speed_entry.get()))
        ctk.CTkLabel(net_frame, text="(0 for unlimited)", font=ctk.CTkFont(size=10, slant="italic")).grid(row=1, column=2, sticky="w")

        ctk.CTkLabel(net_frame, text="Use Browser Cookies:").grid(row=2, column=0, padx=5, pady=5, sticky="e")
        browser_combo = ctk.CTkComboBox(net_frame, values=["None", "chrome", "edge", "firefox", "brave", "opera"], width=120, command=lambda v: self.browser_cookie.set(v))
        browser_combo.set(self.browser_cookie.get())
        browser_combo.grid(row=2, column=1, columnspan=2, sticky="w")

        ctk.CTkLabel(settings_win, text="Processing & Post-Download:", font=ctk.CTkFont(weight="bold")).pack(pady=(15, 0))
        ctk.CTkSwitch(settings_win, text="Embed Metadata & Cover Art (MP3/MP4)", variable=self.embed_metadata).pack(pady=5)
        ctk.CTkSwitch(settings_win, text="Download Auto-Generated Subtitles (.vtt)", variable=self.download_subs).pack(pady=5)
        ctk.CTkSwitch(settings_win, text="Shutdown PC when all downloads finish", variable=self.shutdown_pc).pack(pady=5)

    def add_single(self):
        clipboard_text = ""
        try:
            clip = self.root.clipboard_get()
            if "http" in clip: clipboard_text = clip.strip()
        except tk.TclError: pass 

        dialog = ctk.CTkToplevel(self.root)
        dialog.title("Add Link")
        self.center_toplevel(dialog, 450, 320) # Made taller for the timestamp inputs
        dialog.transient(self.root)
        dialog.grab_set()

        ctk.CTkLabel(dialog, text="Paste YouTube/Playlist URL here:", font=ctk.CTkFont(weight="bold")).pack(pady=(20, 5))
        url_entry = ctk.CTkEntry(dialog, width=380)
        url_entry.pack(pady=5)
        if clipboard_text: url_entry.insert(0, clipboard_text)

        # NEW: Timestamp Clipping UI
        ctk.CTkLabel(dialog, text="Partial Download (Optional):", font=ctk.CTkFont(weight="bold", size=12)).pack(pady=(15, 0))
        time_frame = ctk.CTkFrame(dialog, fg_color="transparent")
        time_frame.pack(pady=5)
        
        ctk.CTkLabel(time_frame, text="Start (mm:ss):").grid(row=0, column=0, padx=5)
        start_entry = ctk.CTkEntry(time_frame, width=80, placeholder_text="00:00")
        start_entry.grid(row=0, column=1, padx=5)
        
        ctk.CTkLabel(time_frame, text="End (mm:ss):").grid(row=0, column=2, padx=5)
        end_entry = ctk.CTkEntry(time_frame, width=80, placeholder_text="End")
        end_entry.grid(row=0, column=3, padx=5)
            
        def submit():
            val = url_entry.get().strip()
            start_val = start_entry.get().strip()
            end_val = end_entry.get().strip()
            
            # Format the range string for the table
            range_str = "Full Video"
            if start_val or end_val:
                s = start_val if start_val else "00:00"
                e = end_val if end_val else "End"
                range_str = f"{s} to {e}"
                
            dialog.destroy()
            if val: self.fetch_and_add(val, range_str)
                
        ctk.CTkButton(dialog, text="Add to Queue", command=submit).pack(pady=20)

    def add_batch(self):
        batch_win = ctk.CTkToplevel(self.root)
        batch_win.title("Batch Add Links")
        self.center_toplevel(batch_win, 450, 350)
        batch_win.transient(self.root)
        batch_win.grab_set()
        
        ctk.CTkLabel(batch_win, text="Paste one URL per line:").pack(pady=10)
        text_box = ctk.CTkTextbox(batch_win, height=200, width=400)
        text_box.pack(pady=5)
        
        def process_batch():
            urls = text_box.get("1.0", tk.END).strip().split('\n')
            batch_win.destroy()
            for url in urls:
                if url.strip(): self.fetch_and_add(url.strip(), "Full Video") # Batch ignores clipping
                    
        ctk.CTkButton(batch_win, text="Add All to Queue", command=process_batch).pack(pady=10)

    def remove_selected(self):
        for item in self.tree.selection(): self.tree.delete(item)

    def clear_all(self):
        for item in self.tree.get_children(): self.tree.delete(item)

    # --- Core Downloader Logic ---
    def fetch_and_add(self, url, range_str):
        self.status_label.configure(text="Fetching info...")
        threading.Thread(target=self._fetch_thread, args=(url, range_str), daemon=True).start()

    def _fetch_thread(self, url, range_str):
        ydl_opts = {'extract_flat': True, 'quiet': True}
        if self.browser_cookie.get() != "None":
            ydl_opts['cookiesfrombrowser'] = (self.browser_cookie.get(),)

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                
            entries = info.get('entries', [info])
            
            def update_ui():
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
                        # Insert into the tree with the new Range column
                        item_id = self.tree.insert("", "end", values=(idx, title, vid_url, range_str, current_format, "Queued"))
                        new_item_ids.append(item_id)
                        
                if new_item_ids: self.tree.selection_set(new_item_ids)
                self.status_label.configure(text="Ready")
                
            self.root.after(0, update_ui)
        except Exception as e:
            self.root.after(0, lambda: self.status_label.configure(text="Error fetching link (Try linking browser cookies)"))

    def download_all(self):
        all_items = self.tree.get_children()
        if not all_items:
            messagebox.showwarning("Warning", "The queue is empty.", parent=self.root)
            return
            
        self.status_label.configure(text="Processing Batch...")
        self.progress_bar.set(0)
        self.completed_items = 0
        self.total_items = len(all_items)
        
        # Start the thread pool executor in the background
        threading.Thread(target=self._run_thread_pool, args=(all_items,), daemon=True).start()

    def _run_thread_pool(self, items):
        max_workers = int(self.concurrent_downloads.get())
        
        # NEW: Concurrent execution using ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = [executor.submit(self._download_single_item, item) for item in items]
            concurrent.futures.wait(futures)
            
        # When all threads are completely finished
        self.root.after(0, lambda: self.status_label.configure(text="All downloads in queue complete!"))
        self.root.after(0, lambda: self.progress_bar.set(1)) 

        if self.shutdown_pc.get():
            os.system("shutdown /s /t 10") 

    def _download_single_item(self, item):
        values = self.tree.item(item, "values")
        title, url, time_range, media_type, status = values[1], values[2], values[3], values[4], values[5]
        
        if status == "Done":
            self._increment_global_progress()
            return 
            
        self.root.after(0, lambda: self.tree.set(item, "Status", "Starting..."))

        safe_title = re.sub(r'[\\/*?:"<>|]', "", title).strip()
        
        # --- Base Options ---
        ydl_opts = {
            'quiet': True,
            'ffmpeg_location': get_ffmpeg_path(),
            'ignoreerrors': True,
            # We inject a CUSTOM hook function that knows exactly which row to update
            'progress_hooks': [self.create_per_item_hook(item)],
            'download_archive': os.path.join(self.download_folder, 'download_history.txt') 
        }

        # Handle Timestamps/Clipping
        if time_range != "Full Video":
            start_str, end_str = time_range.split(" to ")
            start_sec = parse_time(start_str)
            end_sec = parse_time(end_str)
            
            s = start_sec if start_sec is not None else 0
            e = end_sec if end_sec is not None else float('inf')
            
            # This forces yt-dlp to download only the specified range using ffmpeg
            ydl_opts['download_ranges'] = download_range_func(None, [(s, e)])
            ydl_opts['force_keyframes_at_cuts'] = True # Ensures clean cuts

        if self.browser_cookie.get() != "None":
            ydl_opts['cookiesfrombrowser'] = (self.browser_cookie.get(),)

        if self.speed_limit.get().isdigit() and int(self.speed_limit.get()) > 0:
            ydl_opts['ratelimit'] = int(self.speed_limit.get()) * 1024 * 1024 

        if self.download_subs.get() and "Thumb" not in media_type:
            ydl_opts['writesubtitles'] = True
            ydl_opts['writeautomaticsub'] = True
            ydl_opts['subtitleslangs'] = ['en']

        # Logic branches
        if "Thumb" in media_type:
            ext_choice = re.search(r'\((.*?)\)', media_type).group(1)
            ydl_opts['outtmpl'] = os.path.join(self.download_folder, f'{safe_title}.%(ext)s')
            ydl_opts['skip_download'] = True 
            ydl_opts['writethumbnail'] = True
            ydl_opts['postprocessors'] = [{'key': 'FFmpegThumbnailsConvertor', 'format': ext_choice, 'when': 'before_dl'}]
            ydl_opts.pop('download_archive', None) 
            
        elif "Audio" in media_type:
            ext_choice = "mp4" 
            if "wav" in media_type: ext_choice = "wav"
            elif "m4a" in media_type: ext_choice = "m4a"
            else: ext_choice = "mp3"
            
            ydl_opts['outtmpl'] = os.path.join(self.download_folder, f'{safe_title}.{ext_choice}')
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
            ext_choice = "mp4"
            res = re.search(r'\((.*?)\)', media_type).group(1) 
            ydl_opts['outtmpl'] = os.path.join(self.download_folder, f'{safe_title}_{res}.{ext_choice}')
            
            res_map = {"4K": 2160, "1440p": 1440, "1080p": 1080, "720p": 720, "480p": 480, "360p": 360, "240p": 240}
            target_height = 1080
            for key, val in res_map.items():
                if key in media_type:
                    target_height = val
                    break

            ydl_opts['format'] = f'bestvideo[height<={target_height}][vcodec^=avc1]+bestaudio[ext=m4a]/best'
            ydl_opts['merge_output_format'] = 'mp4'
            ydl_opts['postprocessors'] = []
            
            if self.embed_metadata.get():
                ydl_opts['writethumbnail'] = True
                ydl_opts['postprocessors'].extend([{'key': 'EmbedThumbnail'}, {'key': 'FFmpegMetadata'}])

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([url])
            self.root.after(0, lambda i=item: self.tree.set(i, "Status", "Done"))
        except Exception:
            self.root.after(0, lambda i=item: self.tree.set(i, "Status", "Error"))
            
        self._increment_global_progress()

    def _increment_global_progress(self):
        with self.download_lock:
            self.completed_items += 1
            progress = self.completed_items / self.total_items
            self.root.after(0, lambda p=progress: self.progress_bar.set(p))
            self.root.after(0, lambda c=self.completed_items, t=self.total_items: self.status_label.configure(text=f"Completed {c} of {t} files"))

    # NEW: Factory function that generates a specific hook for a specific row in the table
    def create_per_item_hook(self, item_id):
        def hook(d):
            if d['status'] == 'downloading':
                percent = d.get('_percent_str', '0%').strip()
                speed = d.get('_speed_str', 'N/A').strip()
                
                # Update the specific row with its own live stats
                self.root.after(0, lambda i=item_id, p=percent, s=speed: self.tree.set(i, "Status", f"{p} ({s})"))
                
            elif d['status'] == 'finished':
                self.root.after(0, lambda i=item_id: self.tree.set(i, "Status", "Finalizing/Converting..."))
        return hook

if __name__ == "__main__":
    root = ctk.CTk()
    app = DownloadManagerApp(root)
    root.mainloop()