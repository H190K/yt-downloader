import customtkinter
import yt_dlp
import os
import sys
import threading
import subprocess
from PIL import Image, ImageTk
import requests
from io import BytesIO
import tkinter.filedialog
import tkinter
import webbrowser
import queue
import time
import uuid
import json

def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        base_path = sys._MEIPASS
    except Exception:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, relative_path)

class DownloadWorker:
    """Background worker for handling downloads"""
    def __init__(self, gui_callback):
        self.gui_callback = gui_callback
        self.task_queue = queue.Queue()
        self.active_downloads = {}
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()
    
    def _worker_loop(self):
        """Main worker loop"""
        while True:
            try:
                task = self.task_queue.get(timeout=1)
                if task['action'] == 'fetch_details':
                    self._fetch_video_details(task)
                elif task['action'] == 'download':
                    self._download_video(task)
                self.task_queue.task_done()
            except queue.Empty:
                continue
            except Exception as e:
                print(f"Worker error: {e}")
    
    def _fetch_video_details(self, task):
        """Fetch video details in background"""
        try:
            url = task['url']
            ydl_opts = {
                'quiet': True,
                'simulate': True,
                'force_generic_extractor': True,
                'dump_single_json': True,
            }
            
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info_dict = ydl.extract_info(url, download=False)
            
            # Extract relevant information
            video_info = {
                'title': info_dict.get('title', 'N/A'),
                'thumbnail': info_dict.get('thumbnail'),
                'duration': info_dict.get('duration'),
                'uploader': info_dict.get('uploader'),
                'formats': self._extract_formats(info_dict.get('formats', []))
            }
            
            self.gui_callback('video_details_success', video_info)
            
        except Exception as e:
            self.gui_callback('video_details_error', str(e))
    
    def _extract_formats(self, formats):
        """Extract and organize available formats with simplified quality options"""
        organized_formats = {
            'mp4': [],
            'audio': []
        }
        
        # Add "Best" option first
        organized_formats['mp4'].append({
            'quality': 'Best Quality',
            'format_id': 'bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/bestvideo+bestaudio/best',
            'note': 'Highest quality with audio and video merged'
        })
        
        organized_formats['audio'].append({
            'quality': 'Best Audio Quality',
            'format_id': 'bestaudio/best',
            'note': 'Highest quality audio'
        })
        
        # Define standard quality levels
        quality_levels = [
            (240, '240p'),
            (360, '360p'), 
            (480, '480p'),
            (720, '720p'),
            (1080, '1080p'),
            (1440, '2K'),
            (2160, '4K'),
            (4320, '8K')
        ]
        
        # Find actual available video heights
        available_heights = set()
        audio_formats = []
        
        for f in formats:
            if not f.get('format_id'):
                continue
                
            vcodec = f.get('vcodec', 'none')
            acodec = f.get('acodec', 'none')
            height = f.get('height')
            abr = f.get('abr')
            
            # Collect video heights
            if vcodec != 'none' and height:
                available_heights.add(height)
            
            # Collect audio formats
            elif acodec != 'none' and vcodec == 'none' and abr and abr >= 128:
                audio_formats.append({
                    'quality': f"{int(abr)}kbps",
                    'format_id': f['format_id'],
                    'abr': abr
                })
        
        # Only add quality levels that are actually available
        for standard_height, quality_name in quality_levels:
            # Check if we have video at or below this quality level
            available_at_quality = [h for h in available_heights if h <= standard_height]
            if available_at_quality:
                # Use the highest available height for this quality level
                actual_height = max(available_at_quality)
                
                # Create format specification that ensures audio+video
                if actual_height >= 1080:
                    # For high quality, explicitly merge video and audio
                    format_spec = f"bestvideo[height<={actual_height}][ext=mp4]+bestaudio[ext=m4a]/bestvideo[height<={actual_height}]+bestaudio/best[height<={actual_height}]"
                else:
                    # For lower quality, try combined format first, then merge if needed
                    format_spec = f"best[height<={actual_height}][ext=mp4]/bestvideo[height<={actual_height}]+bestaudio/best[height<={actual_height}]"
                
                organized_formats['mp4'].append({
                    'quality': quality_name,
                    'format_id': format_spec,
                    'height': actual_height,
                    'standard_height': standard_height
                })
                
                # Remove this height from available_heights to avoid duplicates
                available_heights = {h for h in available_heights if h > standard_height}
        
        # Add unique audio formats
        seen_bitrates = set()
        for audio_fmt in sorted(audio_formats, key=lambda x: x['abr'], reverse=True):
            if audio_fmt['abr'] not in seen_bitrates:
                seen_bitrates.add(audio_fmt['abr'])
                organized_formats['audio'].append(audio_fmt)
        
        return organized_formats
    
    def _download_video(self, task):
        """Download video in background"""
        download_id = str(uuid.uuid4())
        
        try:
            url = task['url']
            format_type = task['format_type']
            format_id = task['format_id']
            download_path = task['download_path']
            
            def progress_hook(d):
                if d['status'] == 'downloading':
                    total_bytes = d.get('total_bytes') or d.get('total_bytes_estimate')
                    downloaded_bytes = d.get('downloaded_bytes')
                    if total_bytes and downloaded_bytes:
                        progress = downloaded_bytes / total_bytes
                        self.gui_callback('download_progress', {
                            'progress': progress,
                            'percent': d.get('_percent_str', ''),
                            'speed': d.get('_speed_str', ''),
                            'eta': d.get('_eta_str', '')
                        })
                elif d['status'] == 'finished':
                    self.gui_callback('download_processing', None)
                elif d['status'] == 'error':
                    self.gui_callback('download_error', d.get('error', 'Unknown error'))
            
            # Configure download options without current date
            output_template = os.path.join(download_path, '%(title)s.%(ext)s')
            ydl_opts = {
                'outtmpl': output_template,
                'progress_hooks': [progress_hook],
                'postprocessors': [],
                'noplaylist': True,
                'no_warnings': False,
                'extractaudio': False,
                'audioformat': 'best',
                'embed_subs': False,
                'writesubtitles': False,
                'writeautomaticsub': False,
                'allsubtitles': False,
                'ignoreerrors': False,
                'no_overwrites': False,
                'continuedl': True,
                'noprogress': False,
                'consoletitle': False,
                'nopart': False,
                'updatetime': False,  # Don't update file modification time to video upload date
            }
            
            # Set ffmpeg path (local first, then system PATH)
            ffmpeg_paths = [
                resource_path(os.path.join("ffmpeg", "bin", "ffmpeg.exe")),  # Local ffmpeg (PyInstaller compatible)
                "ffmpeg"  # System PATH
            ]
            
            for ffmpeg_path in ffmpeg_paths:
                try:
                    subprocess.run([ffmpeg_path, "-version"], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                    ydl_opts['ffmpeg_location'] = os.path.dirname(ffmpeg_path) if ffmpeg_path.endswith('.exe') else None
                    break
                except (subprocess.CalledProcessError, FileNotFoundError):
                    continue
            
            if format_type == "mp4":
                # Use the format specification as is (already includes audio merging logic)
                ydl_opts['format'] = format_id
                ydl_opts['merge_output_format'] = 'mp4'
                # Ensure we always try to get the best available format with fallbacks
                ydl_opts['format_sort'] = ['res', 'ext:mp4:m4a']
                
            elif format_type == "m4a":
                # For M4A, extract audio from the selected format
                ydl_opts['format'] = format_id
                ydl_opts['postprocessors'].append({
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'm4a',
                })
                output_template = os.path.join(download_path, '%(title)s.m4a')
                ydl_opts['outtmpl'] = output_template
                
            elif format_type == "mp3":
                # For MP3, always use best audio quality
                ydl_opts['format'] = "bestaudio/best"
                ydl_opts['postprocessors'].append({
                    'key': 'FFmpegExtractAudio',
                    'preferredcodec': 'mp3',
                    'preferredquality': '0',  # Use best quality (VBR)
                    'nopostoverwrites': False,  # Allow overwriting
                })
                # Don't specify .mp3 extension in template - let the postprocessor handle it
                output_template = os.path.join(download_path, '%(title)s.%(ext)s')
                ydl_opts['outtmpl'] = output_template
            
            # Perform download with fallback handling
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])
                self.gui_callback('download_complete', task.get('title', 'Video'))
            except Exception as download_error:
                # If the specific format fails, try with a more generic fallback
                if format_type == "mp4" and "Requested format is not available" in str(download_error):
                    self.gui_callback('download_processing', "Trying fallback format...")
                    
                    # Fallback to best available format
                    fallback_opts = ydl_opts.copy()
                    fallback_opts['format'] = 'best[ext=mp4]/bestvideo[ext=mp4]+bestaudio[ext=m4a]/best'
                    
                    try:
                        with yt_dlp.YoutubeDL(fallback_opts) as ydl:
                            ydl.download([url])
                        self.gui_callback('download_complete', task.get('title', 'Video'))
                    except Exception as fallback_error:
                        raise download_error  # Raise original error
                else:
                    raise download_error
            
        except Exception as e:
            self.gui_callback('download_error', str(e))
    
    def fetch_video_details(self, url):
        """Queue video details fetch task"""
        self.task_queue.put({
            'action': 'fetch_details',
            'url': url
        })
    
    def start_download(self, url, format_type, format_id, download_path, title):
        """Queue download task"""
        self.task_queue.put({
            'action': 'download',
            'url': url,
            'format_type': format_type,
            'format_id': format_id,
            'download_path': download_path,
            'title': title
        })

class YouTubeDownloaderApp(customtkinter.CTk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Downloader")
        self.geometry("800x650")
        
        # Set custom theme colors
        self.setup_custom_theme()
        
        # Set application icon
        try:
            self.iconbitmap(resource_path("icon.ico"))
        except Exception:
            pass  # Ignore if icon can't be loaded
        
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)
        self.grid_rowconfigure(1, weight=0)  # Footer row

        # Initialize worker
        self.worker = DownloadWorker(self.handle_worker_callback)
        
        # Setup GUI
        self.setup_gui()
        
        # Initialize variables
        self.video_info = None
        self.available_formats = {}
        
        # Check dependencies
        self.check_dependencies()
        
        # Setup keyboard shortcuts
        self.setup_keyboard_shortcuts()

    def setup_custom_theme(self):
        """Setup custom theme colors for better contrast"""
        # Set default appearance mode
        customtkinter.set_appearance_mode("dark")
        
        # Custom color theme for better contrast
        customtkinter.set_default_color_theme("blue")
    
    def setup_keyboard_shortcuts(self):
        """Setup keyboard shortcuts"""
        # Ctrl+V: Paste URL (focus URL entry and paste)
        self.bind('<Control-v>', self.paste_url_shortcut)
        
        # Enter: Fetch details when URL entry is focused
        self.url_entry.bind('<Return>', self.fetch_details_shortcut)
        
        # Ctrl+D: Start download
        self.bind('<Control-d>', self.download_shortcut)
        
        # F5: Refresh app (clear all fields)
        self.bind('<F5>', self.refresh_app_shortcut)
        
        # Focus URL entry by default
        self.url_entry.focus_set()
    
    def paste_url_shortcut(self, event=None):
        """Handle Ctrl+V shortcut"""
        try:
            # Focus URL entry and paste clipboard content
            self.url_entry.focus_set()
            clipboard_content = self.clipboard_get()
            self.url_entry.delete(0, 'end')
            self.url_entry.insert(0, clipboard_content)
        except:
            pass  # Ignore if clipboard is empty or invalid
        return "break"
    
    def fetch_details_shortcut(self, event=None):
        """Handle Enter key in URL entry"""
        if self.url_entry.get().strip():
            self.fetch_video_details()
        return "break"
    
    def download_shortcut(self, event=None):
        """Handle Ctrl+D shortcut"""
        if self.download_button.cget("state") == "normal":
            self.start_download()
        return "break"
    
    def refresh_app_shortcut(self, event=None):
        """Handle F5 shortcut to refresh the app"""
        # Clear URL entry
        self.url_entry.delete(0, 'end')
        
        # Clear video details
        self.video_title_label.configure(text="")
        self.thumbnail_label.configure(image="")
        
        # Reset format selection
        self.format_optionmenu.set("mp4")
        self.quality_optionmenu.configure(values=["N/A"], state="disabled")
        self.quality_optionmenu.set("N/A")
        
        # Reset progress
        self.progress_bar.set(0)
        
        # Clear status
        self.status_label.configure(text="")
        
        # Disable download button
        self.download_button.configure(state="disabled")
        
        # Reset video info
        self.video_info = None
        self.available_formats = {}
        
        # Focus URL entry
        self.url_entry.focus_set()
        
        return "break"

    def setup_gui(self):
        """Setup the GUI components"""
        # Sidebar Frame
        self.sidebar_frame = customtkinter.CTkFrame(self, width=140, corner_radius=0)
        self.sidebar_frame.grid(row=0, column=0, rowspan=1, sticky="nsew")
        self.sidebar_frame.grid_rowconfigure(4, weight=1)

        self.logo_label = customtkinter.CTkLabel(self.sidebar_frame, text="YouTube Downloader", font=customtkinter.CTkFont(size=18, weight="bold"))
        self.logo_label.grid(row=0, column=0, padx=15, pady=15)

        # Dark/Light Mode Toggle with Icons
        self.appearance_mode_label = customtkinter.CTkLabel(self.sidebar_frame, text="Theme:", anchor="w")
        self.appearance_mode_label.grid(row=5, column=0, padx=15, pady=(10, 0))
        
        # Theme options with better colored icons
        theme_options = ["☀️ Light", "🌜 Dark"]
        self.appearance_mode_optionemenu = customtkinter.CTkOptionMenu(
            self.sidebar_frame, 
            values=theme_options,
            command=self.change_appearance_mode_event,
            fg_color="#FFFFFF",
            text_color="#000000",
            button_color="#DDDDDD",
            button_hover_color="#CCCCCC"
        )
        self.appearance_mode_optionemenu.grid(row=6, column=0, padx=15, pady=(5, 15))
        self.appearance_mode_optionemenu.set("🌜 Dark")

        # Download Guide Section
        self.guide_label = customtkinter.CTkLabel(self.sidebar_frame, text="📥 Download Guide", font=customtkinter.CTkFont(size=16, weight="bold"))
        self.guide_label.grid(row=7, column=0, padx=15, pady=(20, 10))
        
        # Guide steps
        guide_steps = [
            "1. Paste YouTube URL",
            "2. Click 'Fetch Details'",
            "3. Choose format (MP4/M4A/MP3)",
            "4. Select quality",
            "5. Set download folder",
            "6. Click 'Download'"
        ]
        
        self.guide_text = customtkinter.CTkLabel(
            self.sidebar_frame, 
            text="\n".join(guide_steps),
            font=customtkinter.CTkFont(size=11),
            justify="left",
            anchor="w"
        )
        self.guide_text.grid(row=8, column=0, padx=15, pady=(0, 10), sticky="w")
        
        # Format Info section
        self.format_info_label = customtkinter.CTkLabel(self.sidebar_frame, text="📁 Format Info", font=customtkinter.CTkFont(size=14, weight="bold"))
        self.format_info_label.grid(row=9, column=0, padx=15, pady=(10, 5))
        
        format_info_text = [
            "• MP4: Video + Audio",
            "• M4A: High-quality audio",
            "• MP3: Universal audio",
            "• Higher quality = larger size"
        ]
        
        self.format_info_text = customtkinter.CTkLabel(
            self.sidebar_frame, 
            text="\n".join(format_info_text),
            font=customtkinter.CTkFont(size=10),
            justify="left",
            anchor="w"
        )
        self.format_info_text.grid(row=10, column=0, padx=15, pady=(0, 10), sticky="w")
        
        # Features section
        self.features_label = customtkinter.CTkLabel(self.sidebar_frame, text="⚡ Features", font=customtkinter.CTkFont(size=14, weight="bold"))
        self.features_label.grid(row=11, column=0, padx=15, pady=(10, 5))
        
        features_text = [
             "• Auto-saves to Downloads folder",
             "• Progress tracking",
             "• Multiple quality options",
             "• Audio extraction",
             "• Batch processing ready"
         ]
        
        self.features_text = customtkinter.CTkLabel(
            self.sidebar_frame, 
            text="\n".join(features_text),
            font=customtkinter.CTkFont(size=10),
            justify="left",
            anchor="w"
        )
        self.features_text.grid(row=12, column=0, padx=15, pady=(0, 10), sticky="w")
        
        # Shortcuts section
        self.shortcuts_label = customtkinter.CTkLabel(self.sidebar_frame, text="⌨️ Shortcuts", font=customtkinter.CTkFont(size=14, weight="bold"))
        self.shortcuts_label.grid(row=13, column=0, padx=15, pady=(10, 5))
        
        shortcuts_text = [
            "• Ctrl+V: Paste URL",
            "• Enter: Fetch details",
            "• Ctrl+D: Start download",
            "• F5: Refresh app"
        ]
        
        self.shortcuts_text = customtkinter.CTkLabel(
            self.sidebar_frame, 
            text="\n".join(shortcuts_text),
            font=customtkinter.CTkFont(size=10),
            justify="left",
            anchor="w"
        )
        self.shortcuts_text.grid(row=14, column=0, padx=15, pady=(0, 15), sticky="w")

        # Main Frame with scrollable content
        self.main_frame = customtkinter.CTkScrollableFrame(self)
        self.main_frame.grid(row=0, column=1, padx=15, pady=15, sticky="nsew")
        self.main_frame.grid_columnconfigure(0, weight=1)

        # URL Input Section
        self.url_frame = customtkinter.CTkFrame(self.main_frame)
        self.url_frame.grid(row=0, column=0, padx=5, pady=5, sticky="ew")
        self.url_frame.grid_columnconfigure(0, weight=1)
        
        self.url_label = customtkinter.CTkLabel(self.url_frame, text="YouTube URL:")
        self.url_label.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")
        self.url_entry = customtkinter.CTkEntry(self.url_frame, placeholder_text="Enter YouTube URL here")
        self.url_entry.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        self.fetch_button = customtkinter.CTkButton(
            self.url_frame, 
            text="Fetch Details", 
            command=self.fetch_video_details,
            fg_color="#FFFFFF",
            text_color="#000000"
        )
        self.fetch_button.grid(row=2, column=0, padx=10, pady=(5, 10), sticky="ew")

        # Video Details Section
        self.details_frame = customtkinter.CTkFrame(self.main_frame)
        self.details_frame.grid(row=1, column=0, padx=5, pady=5, sticky="ew")
        self.details_frame.grid_columnconfigure(0, weight=1)
        
        self.video_title_label = customtkinter.CTkLabel(self.details_frame, text="", font=customtkinter.CTkFont(size=14, weight="bold"), wraplength=450)
        self.video_title_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")
        self.thumbnail_label = customtkinter.CTkLabel(self.details_frame, text="")
        self.thumbnail_label.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="w")

        # Format and Quality Selection Section
        self.format_frame = customtkinter.CTkFrame(self.main_frame)
        self.format_frame.grid(row=2, column=0, padx=5, pady=5, sticky="ew")
        self.format_frame.grid_columnconfigure(0, weight=1)
        self.format_frame.grid_columnconfigure(1, weight=1)
        
        self.format_label = customtkinter.CTkLabel(self.format_frame, text="Format:")
        self.format_label.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")
        self.quality_label = customtkinter.CTkLabel(self.format_frame, text="Quality:")
        self.quality_label.grid(row=0, column=1, padx=10, pady=(10, 5), sticky="w")
        
        self.format_optionmenu = customtkinter.CTkOptionMenu(
            self.format_frame, 
            values=["mp4", "m4a", "mp3"], 
            command=self.on_format_selected,
            fg_color="#FFFFFF",
            text_color="#000000",
            button_color="#DDDDDD",
            button_hover_color="#CCCCCC"
        )
        self.format_optionmenu.grid(row=1, column=0, padx=10, pady=5, sticky="ew")
        self.format_optionmenu.set("mp4")
        
        self.quality_optionmenu = customtkinter.CTkOptionMenu(
            self.format_frame, 
            values=["N/A"],
            fg_color="#FFFFFF",
            text_color="#000000",
            button_color="#DDDDDD",
            button_hover_color="#CCCCCC"
        )
        self.quality_optionmenu.grid(row=1, column=1, padx=10, pady=5, sticky="ew")
        self.quality_optionmenu.set("N/A")
        self.quality_optionmenu.configure(state="disabled")

        # Download Directory Section
        self.dir_frame = customtkinter.CTkFrame(self.main_frame)
        self.dir_frame.grid(row=3, column=0, padx=5, pady=5, sticky="ew")
        self.dir_frame.grid_columnconfigure(0, weight=1)
        
        self.download_dir_label = customtkinter.CTkLabel(self.dir_frame, text="Download Directory:")
        self.download_dir_label.grid(row=0, column=0, padx=10, pady=(10, 5), sticky="w")
        
        self.dir_input_frame = customtkinter.CTkFrame(self.dir_frame, fg_color="transparent")
        self.dir_input_frame.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        self.dir_input_frame.grid_columnconfigure(0, weight=1)
        
        self.download_dir_entry = customtkinter.CTkEntry(self.dir_input_frame, placeholder_text="Default: Current Directory")
        self.download_dir_entry.grid(row=0, column=0, padx=(0, 5), pady=0, sticky="ew")
        self.browse_button = customtkinter.CTkButton(
            self.dir_input_frame, 
            text="Browse", 
            command=self.browse_download_directory, 
            width=80,
            fg_color="#FFFFFF",
            text_color="#000000"
        )
        self.browse_button.grid(row=0, column=1, padx=0, pady=0)
        
        # Configuration file path
        self.config_file = os.path.join(os.path.expanduser("~"), ".youtube_downloader_config.json")
        
        # Load saved download path or set default
        self.download_path = self.load_download_path()
        
        # Create downloads directory if it doesn't exist
        os.makedirs(self.download_path, exist_ok=True)
        
        self.download_dir_entry.insert(0, self.download_path)
        
        # Bind event to save path when user manually changes it
        self.download_dir_entry.bind("<FocusOut>", self.on_download_path_changed)
        self.download_dir_entry.bind("<Return>", self.on_download_path_changed)

        # Download and Progress Section
        self.download_frame = customtkinter.CTkFrame(self.main_frame)
        self.download_frame.grid(row=4, column=0, padx=5, pady=5, sticky="ew")
        self.download_frame.grid_columnconfigure(0, weight=1)
        
        self.download_button = customtkinter.CTkButton(
            self.download_frame, 
            text="Download", 
            command=self.start_download, 
            height=40,
            fg_color="#FFFFFF",
            text_color="#000000"
        )
        self.download_button.grid(row=0, column=0, padx=10, pady=10, sticky="ew")
        self.download_button.configure(state="disabled")

        self.progress_bar = customtkinter.CTkProgressBar(self.download_frame)
        self.progress_bar.grid(row=1, column=0, padx=10, pady=(0, 10), sticky="ew")
        self.progress_bar.set(0)

        # Status Section
        self.status_frame = customtkinter.CTkFrame(self.main_frame)
        self.status_frame.grid(row=5, column=0, padx=5, pady=5, sticky="ew")
        self.status_frame.grid_columnconfigure(0, weight=1)
        
        self.status_label = customtkinter.CTkLabel(self.status_frame, text="", wraplength=450, justify="left")
        self.status_label.grid(row=0, column=0, padx=10, pady=10, sticky="w")

        # Footer with branding (spans both columns)
        self.footer_frame = customtkinter.CTkFrame(self, height=40, corner_radius=0)
        self.footer_frame.grid(row=1, column=0, columnspan=2, sticky="ew", padx=0, pady=0)
        self.footer_frame.grid_columnconfigure(0, weight=1)
        self.footer_frame.grid_propagate(False)  # Maintain fixed height
        
        # Created by label with clickable link
        self.footer_label = customtkinter.CTkLabel(
            self.footer_frame, 
            text="Created by h190k", 
            font=customtkinter.CTkFont(size=12),
            cursor="hand2"
        )
        self.footer_label.grid(row=0, column=0, pady=10)
        self.footer_label.bind("<Button-1>", self.open_creator_website)

    def check_dependencies(self):
        """Check for required dependencies"""
        # Check for yt-dlp (as Python module)
        try:
            import yt_dlp
            # Test if yt-dlp module works by getting version
            version = yt_dlp.version.__version__
            self.status_label.configure(text="yt-dlp found.", text_color="green")
        except (ImportError, AttributeError) as e:
            self.status_label.configure(text="Error: yt-dlp module not found. Please install it (e.g., pip install yt-dlp).", text_color="red")
            self.fetch_button.configure(state="disabled")
            self.download_button.configure(state="disabled")
            return

        # Check for ffmpeg (local first, then system PATH)
        ffmpeg_found = False
        ffmpeg_paths = [
            resource_path(os.path.join("ffmpeg", "bin", "ffmpeg.exe")),  # Local ffmpeg (PyInstaller compatible)
            "ffmpeg"  # System PATH
        ]
        
        for ffmpeg_path in ffmpeg_paths:
            try:
                subprocess.run([ffmpeg_path, "-version"], check=True, capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
                self.status_label.configure(text=self.status_label.cget("text") + "\nffmpeg found.", text_color="green")
                ffmpeg_found = True
                break
            except (subprocess.CalledProcessError, FileNotFoundError):
                continue
        
        if not ffmpeg_found:
            self.status_label.configure(text=self.status_label.cget("text") + "\nError: ffmpeg not found. MP3 conversion will not work.", text_color="red")
            # Disable mp3 option if ffmpeg is not found
            current_values = list(self.format_optionmenu.cget("values"))
            if "mp3" in current_values:
                current_values.remove("mp3")
                self.format_optionmenu.configure(values=current_values)
                if self.format_optionmenu.get() == "mp3":
                    self.format_optionmenu.set("mp4")

    def handle_worker_callback(self, event_type, data):
        """Handle callbacks from worker thread"""
        self.after(0, lambda: self._handle_worker_callback_main_thread(event_type, data))
    
    def _handle_worker_callback_main_thread(self, event_type, data):
        """Handle worker callbacks in main thread"""
        if event_type == 'video_details_success':
            self.video_info = data
            self.video_title_label.configure(text=data['title'])
            
            # Load thumbnail
            if data.get('thumbnail'):
                threading.Thread(target=self._load_thumbnail, args=(data['thumbnail'],), daemon=True).start()
            
            # Update format options
            self.on_format_selected(self.format_optionmenu.get())
            self.status_label.configure(text="Details fetched. Select quality and download.", text_color="green")
            self.download_button.configure(state="normal")
            
        elif event_type == 'video_details_error':
            self.status_label.configure(text=f"Error fetching details: {data}", text_color="red")
            self.download_button.configure(state="disabled")
            
        elif event_type == 'download_progress':
            progress = data.get('progress', 0)
            percent = data.get('percent', '')
            speed = data.get('speed', '')
            eta = data.get('eta', '')
            
            self.progress_bar.set(progress)
            self.status_label.configure(text=f"Downloading: {percent} at {speed} ETA {eta}", text_color="blue")
            
        elif event_type == 'download_processing':
            self.status_label.configure(text="Processing...", text_color="blue")
            
        elif event_type == 'download_complete':
            self.progress_bar.set(1)
            self.status_label.configure(text=f"Download complete: {data}", text_color="green")
            self.download_button.configure(state="normal")
            
        elif event_type == 'download_error':
            self.status_label.configure(text=f"Download failed: {data}", text_color="red")
            self.download_button.configure(state="normal")

    def _load_thumbnail(self, url):
        """Load thumbnail image"""
        try:
            response = requests.get(url)
            response.raise_for_status()
            img_data = response.content
            img = Image.open(BytesIO(img_data))
            img.thumbnail((200, 150))
            photo = customtkinter.CTkImage(light_image=img, dark_image=img, size=(img.width, img.height))
            self.after(0, lambda: self.thumbnail_label.configure(image=photo))
            self.after(0, lambda: setattr(self.thumbnail_label, 'image', photo))
        except Exception as e:
            self.after(0, lambda: self.status_label.configure(text=f"Error loading thumbnail: {e}", text_color="orange"))

    def change_appearance_mode_event(self, new_appearance_mode: str):
        """Change appearance mode"""
        # Extract the actual mode from the colored icon-text format
        if "🟡" in new_appearance_mode or "Light" in new_appearance_mode:
            mode = "light"
        elif "🔵" in new_appearance_mode or "Dark" in new_appearance_mode:
            mode = "dark"
        else:
            mode = "dark"  # Default fallback
            
        customtkinter.set_appearance_mode(mode)
        self.update_theme_colors(mode)

    def update_theme_colors(self, mode):
        """Update theme colors for better contrast"""
        if mode == "dark":
            # Dark theme: Really dark background, white buttons
            button_color = "#FFFFFF"
            button_text_color = "#000000"
            option_color = "#FFFFFF"
            option_text_color = "#000000"
            option_button_color = "#DDDDDD"
            option_hover_color = "#CCCCCC"
        elif mode == "light":
            # Light theme: Light background, dark buttons
            button_color = "#2B2B2B"
            button_text_color = "#FFFFFF"
            option_color = "#2B2B2B"
            option_text_color = "#FFFFFF"
            option_button_color = "#404040"
            option_hover_color = "#505050"
        else:
            # Default to dark theme
            button_color = "#FFFFFF"
            button_text_color = "#000000"
            option_color = "#FFFFFF"
            option_text_color = "#000000"
            option_button_color = "#DDDDDD"
            option_hover_color = "#CCCCCC"
        
        # Update button colors for better contrast
        try:
            self.fetch_button.configure(fg_color=button_color, text_color=button_text_color)
            self.browse_button.configure(fg_color=button_color, text_color=button_text_color)
            self.download_button.configure(fg_color=button_color, text_color=button_text_color)
            
            # Update option menu colors
            self.appearance_mode_optionemenu.configure(
                fg_color=option_color, 
                text_color=option_text_color,
                button_color=option_button_color,
                button_hover_color=option_hover_color
            )
            self.format_optionmenu.configure(
                fg_color=option_color, 
                text_color=option_text_color,
                button_color=option_button_color,
                button_hover_color=option_hover_color
            )
            self.quality_optionmenu.configure(
                fg_color=option_color, 
                text_color=option_text_color,
                button_color=option_button_color,
                button_hover_color=option_hover_color
            )
        except:
            pass  # Ignore if buttons don't exist yet

    def load_download_path(self):
        """Load download path from configuration file"""
        try:
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
                    saved_path = config.get('download_path')
                    if saved_path and os.path.exists(saved_path):
                        return saved_path
        except (json.JSONDecodeError, IOError):
            pass
        
        # Return default path if no saved path or saved path doesn't exist
        import platform
        if platform.system() == "Windows":
            return os.path.join(os.path.expanduser("~"), "Downloads")
        else:
            # For macOS and Linux
            return os.path.join(os.path.expanduser("~"), "Downloads")
    
    def save_download_path(self, path):
        """Save download path to configuration file"""
        try:
            config = {}
            if os.path.exists(self.config_file):
                with open(self.config_file, 'r') as f:
                    config = json.load(f)
            
            config['download_path'] = path
            
            with open(self.config_file, 'w') as f:
                json.dump(config, f, indent=2)
        except (json.JSONDecodeError, IOError) as e:
            print(f"Error saving configuration: {e}")

    def open_creator_website(self, event):
        """Open creator website in browser"""
        webbrowser.open("https://h190k.com")

    def on_download_path_changed(self, event):
        """Handle when user manually changes download path"""
        new_path = self.download_dir_entry.get().strip()
        if new_path and os.path.isdir(new_path):
            self.download_path = new_path
            self.save_download_path(self.download_path)
        elif new_path and not os.path.isdir(new_path):
            # If path doesn't exist, revert to current valid path
            self.download_dir_entry.delete(0, customtkinter.END)
            self.download_dir_entry.insert(0, self.download_path)

    def browse_download_directory(self):
        """Browse for download directory"""
        folder_selected = tkinter.filedialog.askdirectory()
        if folder_selected:
            self.download_path = folder_selected
            self.download_dir_entry.delete(0, customtkinter.END)
            self.download_dir_entry.insert(0, self.download_path)
            # Save the selected path to configuration
            self.save_download_path(self.download_path)

    def fetch_video_details(self):
        """Fetch video details"""
        url = self.url_entry.get()
        if not url:
            self.status_label.configure(text="Please enter a YouTube URL.", text_color="red")
            return

        self.status_label.configure(text="Fetching video details...", text_color="blue")
        self.download_button.configure(state="disabled")
        self.quality_optionmenu.configure(state="disabled")
        self.quality_optionmenu.set("N/A")
        self.video_title_label.configure(text="")
        self.thumbnail_label.configure(image=None)
        self.thumbnail_label.image = None

        # Send to worker
        self.worker.fetch_video_details(url)

    def on_format_selected(self, selected_format):
        """Handle format selection"""
        if not self.video_info:
            self.quality_optionmenu.configure(values=["N/A"], state="disabled")
            self.quality_optionmenu.set("N/A")
            return

        formats = self.video_info.get('formats', {})
        quality_options = []
        self.available_formats = {}

        if selected_format == "mp4":
            format_list = formats.get('mp4', [])
            for fmt in format_list:
                quality = fmt['quality']
                self.available_formats[quality] = fmt['format_id']
                quality_options.append(quality)
            
            # Enable quality selection for MP4
            if quality_options:
                self.quality_optionmenu.configure(values=quality_options, state="normal")
                self.quality_optionmenu.set(quality_options[0])
            else:
                self.quality_optionmenu.configure(values=["No suitable quality found"], state="disabled")
                self.quality_optionmenu.set("No suitable quality found")
                
        elif selected_format == "m4a":
            format_list = formats.get('audio', [])
            for fmt in format_list:
                quality = fmt['quality']
                self.available_formats[quality] = fmt['format_id']
                quality_options.append(quality)
            
            # Enable quality selection for M4A
            if quality_options:
                self.quality_optionmenu.configure(values=quality_options, state="normal")
                self.quality_optionmenu.set(quality_options[0])
            else:
                self.quality_optionmenu.configure(values=["No suitable quality found"], state="disabled")
                self.quality_optionmenu.set("No suitable quality found")
                
        elif selected_format == "mp3":
            # For MP3, always use best quality and gray out the selection
            self.available_formats["Best Audio Quality (Auto)"] = "bestaudio/best"
            self.quality_optionmenu.configure(values=["Best Audio Quality (Auto)"], state="disabled")
            self.quality_optionmenu.set("Best Audio Quality (Auto)")

        # Update status if no formats found
        if not quality_options and selected_format != "mp3":
            self.status_label.configure(text=f"No suitable {selected_format} quality found for this video.", text_color="orange")

    def start_download(self):
        """Start download process"""
        url = self.url_entry.get()
        selected_format = self.format_optionmenu.get()
        selected_quality = self.quality_optionmenu.get()

        if not url:
            self.status_label.configure(text="Please enter a YouTube URL.", text_color="red")
            return
        if not self.video_info:
            self.status_label.configure(text="Please fetch video details first.", text_color="red")
            return
        if selected_quality == "N/A" or selected_quality == "No suitable quality found":
            self.status_label.configure(text="Please select a valid quality.", text_color="red")
            return

        format_id = self.available_formats.get(selected_quality)
        if not format_id:
            self.status_label.configure(text="Invalid quality selection.", text_color="red")
            return

        self.progress_bar.set(0)
        self.download_button.configure(state="disabled")
        
        # Send to worker
        self.worker.start_download(url, selected_format, format_id, self.download_path, self.video_info['title'])

if __name__ == "__main__":
    app = YouTubeDownloaderApp()
    app.mainloop()