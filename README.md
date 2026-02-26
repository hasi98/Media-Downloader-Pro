# 🎬 Media Downloader Pro v2  
**A powerful, modern YouTube video & audio downloader with advanced control**

Media Downloader Pro is a desktop application for downloading YouTube videos, playlists, and channels in the highest quality possible. Built for creators, learners, and power users who want speed, control, and reliability.

Whether you're archiving tutorials, saving music, or clipping specific moments — Media Downloader Pro gives you full control with a sleek, modern interface.

---

## ✨ Features

### Core Downloads
- **4K Ultra HD Support**  
  Download videos up to **2160p (4K)** using H.264/AAC for maximum device compatibility.

- **320kbps Audio Extraction**  
  Save high-quality audio in **MP3, WAV, or M4A** formats.

- **Concurrent Downloads**  
  Multi-threaded engine allows downloading **up to 5 files at once** for faster batch jobs.

- **Playlist & Channel Crawler**  
  Browse, select, and download from full playlists or entire channels with an interactive picker.

- **Batch Import**  
  Paste multiple URLs at once to queue them all in one go.

- **Thumbnail Ripper**  
  Extract maximum-resolution thumbnails in one click.

### Precision Editing
- **Time Range Clipping**  
  Download only a specific portion (e.g. `01:15 – 02:45`) without downloading the full video.

- **Visual Trimmer**  
  An interactive range-slider UI to visually select your desired clip timeframe before downloading.

### Smart Features
- **Smart History (SQLite)**  
  Automatically tracks downloaded videos to avoid duplicates and save disk space.

- **Metadata Embedding**  
  Embed thumbnails, titles, and artist metadata directly into downloaded files.

- **Subtitle Downloader**  
  Download subtitles / captions (SRT or VTT) when available.

- **Auto-Convert for Mobile**  
  Automatically convert videos into mobile-friendly formats for phones and tablets.

### Live Dashboard
- **Real-time Bandwidth Graph**  
  60-second scrolling speed graph with live updates.

- **Session Metrics**  
  Track current speed, peak speed, total downloaded, and session time at a glance.

- **System Monitor**  
  CPU, RAM, and disk health indicators updated in real time.

### Remote Control
- **📱 Mobile Remote**  
  Control downloads from your phone via a built-in web server. Scan a QR code to connect.

- **Queue Management**  
  Add links, resume, pause, and stop downloads from the mobile interface.

- **Per-Item Controls**  
  Resume, pause, or stop individual downloads directly from the remote UI.

### Network & Security
- **Authentication Support**  
  Import cookies from Chrome, Edge, Firefox, Brave, or Opera to access age-restricted or private content.

- **Proxy Support**  
  Route downloads through SOCKS5 or HTTP proxies.

- **Speed Limiting**  
  Set maximum download speed (MB/s) to avoid saturating your connection.

- **Aggressive Multi-threading**  
  Optional speed boost using concurrent connections for faster downloads.

### Desktop Integration
- **System Tray**  
  Minimize to tray on close — keeps running in the background without cluttering your taskbar.

- **Desktop Notifications**  
  Get notified when downloads complete (requires `winotify`).

- **Modern Dark UI**  
  Premium dark theme with color-coded controls, accent gradients, and refined typography. Built with CustomTkinter.

---

## 🖥️ System Requirements

- Windows 10 / 11 (64-bit)  
- Python 3.8+ (for running from source)  
- Internet connection  
- ~100MB free disk space  

### Required Python Packages
```
customtkinter, yt-dlp, pillow, psutil, darkdetect
```

### Optional (for extra features)
```
flask, qrcode     → Remote Control
pystray            → System Tray
winotify           → Desktop Notifications
```

---

## 🚀 Installation

### From Source
```bash
pip install customtkinter yt-dlp pillow psutil darkdetect flask qrcode pystray winotify
python "Media Downloader Pro.py"
```

### From Installer
1. Download the latest setup file from the Releases page  
2. Run the installer  
3. Follow the on-screen installation steps  
4. Launch the app from the desktop shortcut  

---

## 📥 How to Use

1. Copy a YouTube video / playlist / channel URL  
2. Paste it into Media Downloader Pro (or press `Ctrl+V`)  
3. Choose:
   - Media type: **Audio / Video / Thumbnail**  
   - Format: **MP3 / WAV / M4A / MP4**  
   - Quality: **1080p / 1440p / 4K**  
   - Bitrate: **128 / 192 / 256 / 320 kbps**  
4. (Optional) Set clip time range or use the **Visual Trimmer**  
5. Click **Download Selected** or **Download All**

Default download location: Downloads Folder (configurable in Settings)

---

## 📱 Remote Control

1. Go to **Settings → Remote Control**  
2. Scan the QR code with your phone  
3. Your phone opens a mobile-friendly web UI  
4. Add links, resume/pause/stop downloads from your phone  

Works on any device connected to the same Wi-Fi network.

---

## 🔐 Cookies & Private Content

To download age-restricted or private videos (that you are authorized to view):

1. Log in to YouTube in Chrome / Edge / Firefox / Brave / Opera  
2. In the app, go to **Settings → Browser Cookies**  
3. Select your browser  
4. Downloads will now use your session's authentication  

🔒 Cookies are stored **locally only** and are never uploaded or shared.

---

## 🔍 Security & Virus Scan Transparency

This installer has been scanned on VirusTotal:

> ✔ VirusTotal: **1/70 detections**  
> Detected only by **Bkav Pro (W64.AIDetectMalware)** – a known heuristic false-positive.  
> All major antivirus engines (Microsoft Defender, Kaspersky, Bitdefender, ESET, Sophos, etc.) report the file as clean.

This is common for custom-built downloader tools that use FFmpeg / yt-dlp and browser cookies.

---

## ⚠️ Legal & Ethical Notice

This software is intended for:
- Personal backups  
- Offline viewing  
- Content you own or have permission to download  

Do **NOT** use this tool to:
- Redistribute copyrighted content  
- Circumvent paywalls or subscriptions  
- Access private content you don't own  

You are responsible for how you use this software.

---

## 🆕 What's New in v2

- **Redesigned UI** — Premium dark theme with color-coded controls and modern typography  
- **Live Dashboard** — Real-time speed graph, session metrics, and system monitor  
- **Mobile Remote Control** — Control downloads from your phone via QR code  
- **Visual Trimmer** — Interactive range slider for precise time-range clipping  
- **Playlist Browser** — Interactive picker to select specific videos from playlists/channels  
- **System Tray & Notifications** — Background operation with desktop alerts  
- **Proxy & Speed Limit** — SOCKS5/HTTP proxy support and bandwidth throttling  
- **Per-Item Remote Controls** — Resume/pause/stop individual downloads from mobile  

---

## 🛠️ Roadmap

- macOS support  
- Linux support  

---

## 💬 Support

For bug reports or feature requests:  
- Open an issue on GitHub  

---

## ⭐ Credits

Built by: **Hasith Lakshan**  
Powered by FFmpeg & yt-dlp  