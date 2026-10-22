#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Civitai Model Scanner CLI
基於 Stable-Diffusion-Webui-Civitai-Helper 的 scan_model 邏輯。
獨立運行的命令行工具，無需 WebUI 介面。
僅生成/更新 .civitai.info 元數據文件。

依賴: requests
安裝: pip install requests

參數說明:
    path                    要掃描的目錄 (必填)。會掃描根目錄及其下所有子目錄。

    --refetch               強制重新從 Civitai API 抓取所有模型的元數據，
                            並重寫 .civitai.info。

    --refetch-only-not-found  只重新抓取先前未找到 (骨架 skeleton) 的模型元數據，
                            已有完整資訊者略過。

    --no-cover              不下載封面預覽圖。

    --download-video        額外下載第一部影片附件 (存為 {模型名}.preview.mp4)。
                            預設只取第一張「圖片」當封面，影片不會被誤存為 .preview.png。

    --attachments-only      只依現有有效的 .civitai.info 重新下載缺漏的封面/影片。
                            完全不呼叫 API、不重算模型 SHA256、不寫入任何 .civitai.info。
                            適合只想補齊附件、不動元數據的情境。

    --force-attachments     略過「附件已存在」檢查，強制重新下載以取代舊檔。
                            適合用來替換可能損毀的 .preview.png。

    --api-base URL          Civitai API 位址 (預設: https://civitai.red)。

    --help                  列出所有參數說明 (由 argparse 內建提供)。

環境變數 (對應上述參數，命令列未指定時採用):
    CIVITAI_HELPER_API_BASE                 對應 --api-base
    CIVITAI_HELPER_REFETCH                  對應 --refetch (1/true/yes)
    CIVITAI_HELPER_REFETCH_ONLY_NOT_FOUND   對應 --refetch-only-not-found (1/true/yes)
    CIVITAI_HELPER_NO_COVER                 對應 --no-cover (1/true/yes)
    CIVITAI_HELPER_DOWNLOAD_VIDEO           對應 --download-video (1/true/yes)
    CIVITAI_HELPER_ATTACHMENTS_ONLY         對應 --attachments-only (1/true/yes)
    CIVITAI_HELPER_FORCE_ATTACHMENTS        對應 --force-attachments (1/true/yes)

使用範例:
    # 掃描目錄，更新元數據並下載封面
    civitai_scanner.bat "D:\\sd-forge\\models\\Stable-diffusion"

    # 只補齊缺漏的封面/影片，完全不動 .civitai.info
    civitai_scanner.bat "D:\\sd-forge\\models\\Stable-diffusion" --attachments-only

    # 強制重建所有封面 (取代可能損毀的 .preview.png)，仍不動 .civitai.info
    civitai_scanner.bat "D:\\sd-forge\\models" --attachments-only --force-attachments

    # 連同第一部影片一起下載
    civitai_scanner.bat "D:\\sd-forge\\models" --attachments-only --download-video
"""

import os
import re
import stat
import sys
import json
import time
import hashlib
import argparse
import requests
from datetime import datetime
from enum import Enum
from functools import lru_cache

# 配置
CIVITAI_API_BASE = "https://civitai.red"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
EXTS = {".bin", ".pt", ".safetensors", ".ckpt", ".gguf", ".zip"}
INFO_SUFFIX = ".civitai.info"
PREVIEW_SUFFIX = ".preview.png"
VIDEO_EXTS = {".mp4", ".webm", ".mov", ".m4v", ".avi"}
SHORT_NAME = "sd_civitai_helper"
VERSION = "1.8.13"  # 保持與主專案一致或自定義
DELAY = 0.5  # API 請求間隔 (秒)

class EnumCoverStatus(Enum):
    """封面圖下載狀態"""
    NO_IMAGE = 0      # 無圖片可用
    EXISTS = 1        # 預覽圖已存在
    DOWNLOADED = 2    # 新下載完成
    FAILED = 3        # 下載失敗

# Windows VT Mode support
def enable_colors():
    """Detect if we can use colors (ANSI) and enable them on Windows if needed."""
    if not sys.stdout.isatty():
        return False

    if os.name == 'nt':
        try:
            from ctypes import windll, byref, c_ulong, create_string_buffer
            hOut = windll.kernel32.GetStdHandle(-11)
            mode = c_ulong()
            if not windll.kernel32.GetConsoleMode(hOut, byref(mode)):
                return False
            # ENABLE_VIRTUAL_TERMINAL_PROCESSING = 0x0004
            mode.value |= 0x0004
            windll.kernel32.SetConsoleMode(hOut, mode)
            return True
        except:
            return False
    return True

USE_COLORS = enable_colors()

class Colors:
    RESET = "\033[0m" if USE_COLORS else ""
    BOLD = "\033[1m" if USE_COLORS else ""
    RED = "\033[91m" if USE_COLORS else ""
    GREEN = "\033[92m" if USE_COLORS else ""
    YELLOW = "\033[93m" if USE_COLORS else ""
    BLUE = "\033[94m" if USE_COLORS else ""
    MAGENTA = "\033[95m" if USE_COLORS else ""
    CYAN = "\033[96m" if USE_COLORS else ""
    GRAY = "\033[90m" if USE_COLORS else ""

def print_log(msg, color=None):
    timestamp = datetime.now().strftime("%H:%M:%S")
    timestamp_str = f"{Colors.GRAY}[{timestamp}]{Colors.RESET}"

    try:
        if color:
            print(f"{timestamp_str} {color}{msg}{Colors.RESET}")
        else:
            print(f"{timestamp_str} {msg}")
    except UnicodeEncodeError:
        # Fallback: encode to console's codepage, replacing unsupported chars
        import sys
        enc = sys.stdout.encoding or 'utf-8'
        safe_msg = msg.encode(enc, errors='replace').decode(enc, errors='replace')
        if color:
            print(f"{timestamp_str} {color}{safe_msg}{Colors.RESET}")
        else:
            print(f"{timestamp_str} {safe_msg}")

def calculate_sha256(filepath):
    """計算文件的 SHA256 哈希值 (與 civitai_report.py 的 compute_sha256 同演算法)"""
    print_log(f"Calculating SHA256 for: {os.path.basename(filepath)}...")
    sha256_hash = hashlib.sha256()
    block_size = 65536  # 64kb

    try:
        with open(filepath, "rb") as f:
            for block in iter(lambda: f.read(block_size), b""):
                sha256_hash.update(block)
        return sha256_hash.hexdigest().upper()
    except Exception as e:
        print_log(f"Error calculating hash: {e}", Colors.RED)
        return None

def get_model_info_from_civitai(model_hash):
    """從 Civitai API 獲取模型資訊"""
    url = f"{CIVITAI_API_BASE}/api/v1/model-versions/by-hash/{model_hash}"
    headers = {"User-Agent": USER_AGENT}

    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        elif response.status_code == 404:
            return None
        else:
            print_log(f"API Error: Status {response.status_code}", Colors.RED)
            return None
    except Exception as e:
        print_log(f"Connection Error: {e}", Colors.RED)
        return None

def create_skeleton_info(filepath, model_hash):
    """創建空的骨架資訊 (Dummy Info)"""
    filename = os.path.basename(filepath)
    try:
        size_kb = os.path.getsize(filepath) // 1024
    except:
        size_kb = 0

    return {
        "id": "",
        "modelId": "",
        "name": filename,
        "trainedWords": [],
        "baseModel": "Unknown",
        "description": "",
        "model": {
            "name": "",
            "type": "",
            "nsfw": "",
            "poi": ""
        },
        "files": [
            {
                "name": filename,
                "sizeKB": size_kb,
                "type": "Model",
                "hashes": {
                    "SHA256": model_hash
                }
            }
        ],
        "tags": [],
        "downloadUrl": "",
        "skeleton_file": True
    }

def process_model_info(info, is_skeleton=False, model_hash=None):
    """處理模型資訊，添加插件擴展字段"""
    info["skeleton_file"] = None

    # 確保 extensions 字段存在
    if "extensions" not in info:
        info["extensions"] = {}

    # 添加 sd_civitai_helper 版本資訊
    info["extensions"][SHORT_NAME] = {
        "version": VERSION,
        "last_update": int(time.time()),
        "skeleton_file": is_skeleton
    }

    if model_hash:
        info["extensions"]["local_sha256"] = model_hash

    return info

def get_attachment_kind(item):
    """判斷 images 陣列中單筆附件的類型
    Civitai 的 images 陣列可能混有 video (mp4/webm) 與 image，
    有 type 欄位時以 type 為準，否則用 URL 副檔名判斷。
    回傳: "video" / "image"
    """
    item_type = (item.get("type") or "").lower()
    if item_type in ("video", "image"):
        return item_type

    # 沒有 type 欄位時，退回用 URL 副檔名判斷 (忽略 query string)
    url_no_query = (item.get("url") or "").split("?")[0].lower()
    ext = os.path.splitext(url_no_query)[1]
    if ext in VIDEO_EXTS:
        return "video"
    return "image"

def download_cover_image(filepath, info, max_size=True, force=False):
    """下載模型的封面預覽圖
    封面取第一個「圖片」附件，跳過 video 類型，避免把影片存成 .preview.png
    force=True 時略過「已存在」檢查，重新下載以取代可能損毀的舊檔
    返回: CoverStatus 枚舉值
    """
    # 檢查是否已有預覽圖
    base, _ = os.path.splitext(filepath)
    preview_path = f"{base}{PREVIEW_SUFFIX}"

    if os.path.exists(preview_path) and not force:
        return EnumCoverStatus.EXISTS

    # 從 API 回應中獲取圖片列表
    images = info.get("images", [])
    if not images:
        return EnumCoverStatus.NO_IMAGE

    # 選擇第一個圖片附件 (跳過 video，否則會下載到 mp4 導致預覽圖損毀)
    img = next((item for item in images if get_attachment_kind(item) == "image"), None)
    if img is None:
        return EnumCoverStatus.NO_IMAGE

    img_url = img.get("url", "")
    if not img_url:
        return EnumCoverStatus.NO_IMAGE

    # 如果需要最大尺寸，替換 URL 中的寬度參數
    if max_size:
        width = img.get("width", 0)
        if width:
            img_url = re.sub(r'/width=\d+/', f'/width={width}/', img_url)

    # 下載圖片
    headers = {"User-Agent": USER_AGENT}
    try:
        response = requests.get(img_url, headers=headers, timeout=30, stream=True)
        if response.status_code == 200:
            with open(preview_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            return EnumCoverStatus.DOWNLOADED
    except Exception as e:
        print_log(f"Cover download error: {e}", Colors.YELLOW)

    return EnumCoverStatus.FAILED

def try_download_cover(filepath, info_path, stats_obj, progress_prefix, max_size_preview, info=None, force=False):
    """嘗試下載封面圖 (統一邏輯)
    返回: True 如果成功下載了新封面
    """
    # 如果沒有提供 info，從 info 檔案讀取
    if info is None:
        if os.path.exists(info_path):
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    info = json.load(f)
            except Exception:
                pass

    if not info or not info.get("images"):
        return False

    result = download_cover_image(filepath, info, max_size_preview, force)
    if result == EnumCoverStatus.DOWNLOADED:
        stats_obj["covers"] += 1
        model_name = info.get('model', {}).get('name', 'Unknown')
        print_log(f"{progress_prefix} Cover downloaded: {Colors.GREEN}{model_name}", Colors.RESET)
        return True

    return False

def download_video_file(filepath, info, stats_obj, progress_prefix, force=False):
    """下載 info 中第一部 video 附件 (選擇性功能)
    只下載 images 陣列中第一部影片，不抓全部
    存檔名: {base}.preview.mp4
    已存在的影片會跳過 (force=True 時重新下載)
    返回: True 如果成功下載
    """
    vid = next((item for item in info.get("images", [])
                if get_attachment_kind(item) == "video" and item.get("url")), None)
    if vid is None:
        return False

    base, _ = os.path.splitext(filepath)
    headers = {"User-Agent": USER_AGENT}

    # 依 URL 副檔名存檔，抓不到時預設 .mp4
    url_no_query = vid["url"].split("?")[0]
    vid_ext = os.path.splitext(url_no_query)[1].lower()
    if vid_ext not in VIDEO_EXTS:
        vid_ext = ".mp4"

    video_path = f"{base}.preview{vid_ext}"
    if os.path.exists(video_path) and not force:
        return False

    try:
        response = requests.get(vid["url"], headers=headers, timeout=60, stream=True)
        if response.status_code == 200:
            with open(video_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
            stats_obj["videos"] += 1
            print_log(f"{progress_prefix} Video downloaded: {Colors.GREEN}{os.path.basename(video_path)}", Colors.RESET)
            return True
        else:
            print_log(f"{progress_prefix} Video HTTP {response.status_code}: {vid['url']}", Colors.YELLOW)
    except Exception as e:
        print_log(f"{progress_prefix} Video download error: {e}", Colors.YELLOW)

    return False

def save_info_file(filepath, info):
    """保存 .civitai.info 文件"""
    base, _ = os.path.splitext(filepath)
    info_path = f"{base}{INFO_SUFFIX}"

    try:
        with open(info_path, 'w', encoding='utf-8') as f:
            json.dump(info, f, indent=4, ensure_ascii=False)
        # print_log(f"Saved metadata: {os.path.basename(info_path)}") # 減少日誌噪音
        return True
    except Exception as e:
        print_log(f"Error saving file: {e}", Colors.RED)
        return False

def metadata_needed(filepath, refetch_old, refetch_only_not_found=False):
    """檢查是否需要掃描"""
    base, _ = os.path.splitext(filepath)
    info_path = f"{base}{INFO_SUFFIX}"

    if refetch_old:
        return True

    if not os.path.exists(info_path):
        return True

    if refetch_only_not_found:
        try:
            with open(info_path, 'r', encoding='utf-8') as f:
                info = json.load(f)

            # Check for skeleton_file flag
            if info.get("skeleton_file", False):
                return True

            # Also check extension flag (newer format)
            exts = info.get("extensions", {})
            if exts.get(SHORT_NAME, {}).get("skeleton_file", False):
                return True
        except:
             # If file is corrupted, re-fetch
             return True

    # 如果文件存在且不強制更新，則跳過
    # 這裡可以加入版本檢查邏輯，但為了簡化CLI，預設存在即跳過
    return False

def print_summary(title, stats, scan_root=None):
    """Helper to print stats summary"""
    border = f"{Colors.GRAY}{'-'*50}{Colors.RESET}"
    print("\n" + border)
    print(f"{Colors.BOLD}Summary for: {Colors.CYAN}{title}{Colors.RESET}")
    print(f"Files Scanned: {stats['processed']}")
    print(f"Ignored:       {Colors.GRAY}{stats['ignored']}{Colors.RESET}")

    # Updated (Green if > 0)
    updated_color = Colors.GREEN if stats['updated'] > 0 else ""
    print(f"Updated:       {updated_color}{stats['updated']}{Colors.RESET}")

    # Covers downloaded (Cyan if > 0)
    covers_count = stats.get('covers', 0)
    covers_color = Colors.CYAN if covers_count > 0 else ""
    print(f"Covers:        {covers_color}{covers_count}{Colors.RESET}")

    # Videos downloaded (Magenta if > 0)
    video_count = stats.get('videos', 0)
    video_color = Colors.MAGENTA if video_count > 0 else ""
    print(f"Videos:        {video_color}{video_count}{Colors.RESET}")

    # Duplicates (Blue if > 0)
    dup_count = len(stats['duplicates'])
    dup_color = Colors.BLUE if dup_count > 0 else ""
    print(f"Duplicates:    {dup_color}{dup_count}{Colors.RESET}")


    # Not Found (Yellow if > 0)
    nf_color = Colors.YELLOW if stats['not_found'] > 0 else ""
    print(f"Not Found:     {nf_color}{stats['not_found']}{Colors.RESET}")

    # Failed (Red if > 0)
    if stats['failed'] > 0:
        print(f"Failed:        {Colors.RED}{stats['failed']}{Colors.RESET}")

    # 顯示重複檔案路徑 (分組顯示)
    if dup_count > 0 and scan_root:
        print(f"\n{Colors.GRAY}[Duplicate Files List]{Colors.RESET}")

        # Group by hash
        groups = {}
        for item in stats['duplicates']:
            if isinstance(item, tuple):
                p_abs, h = item
                short_h = h[:8]
                if short_h not in groups:
                    groups[short_h] = []
                groups[short_h].append(p_abs)

        for h, paths in groups.items():
            print(f"  Hash: {Colors.BLUE}{h}...{Colors.RESET}")
            for p_abs in paths:
                try:
                    p_rel = os.path.relpath(p_abs, start=scan_root)
                    link_info = get_link_type(p_abs, scan_root)
                    print(f"    - {p_rel}{link_info}")
                except Exception:
                    print(f"    - {p_abs}")

    print(border + "\n")

@lru_cache(maxsize=None)
def is_junction_or_link(path):
    """
    在 Windows 上用 os.lstat() 搭配 stat.FILE_ATTRIBUTE_REPARSE_POINT
    可以先判斷「這個路徑是不是某種 reparse point」。
    接著再用 os.path.islink() 判斷是否為 symlink。
    如果是 reparse point 但不是 symlink，那麼它很可能就是 junction。
    """
    link_type = ""
    try:
        if os.path.islink(path):
            link_type = "Symlink"
        else:
            is_dir = os.path.isdir(path)

            st = os.lstat(path)
            if is_dir and st.st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                link_type = "Junction"
            elif not is_dir and st.st_nlink > 1:
                link_type = "Hardlink"
    except:
        if link_type:
            return link_type
        return None

    return link_type

@lru_cache(maxsize=None)
def get_link_type(filepath, root_directory):
    """(Lazy) Detect link type for a specific file"""
    try:
        filepath = os.path.abspath(filepath)
        root_directory = os.path.abspath(root_directory)
    except:
        return ""

    # Check file itself
    link_type = is_junction_or_link(filepath)
    if link_type == "Symlink" or link_type == "Hardlink":
        return f" {Colors.CYAN}({link_type}){Colors.RESET}"

    # Junction/Link check in parents
    check_dir = os.path.dirname(filepath)
    root_norm = os.path.normcase(root_directory)

    while True:
        # Check if current dir is a link/junction
        # Use helper check
        ptype = is_junction_or_link(check_dir)
        if ptype:
             return f" {Colors.CYAN}({ptype}){Colors.RESET}"

        # Stop if we strictly reached the scan root
        if os.path.normcase(check_dir) == root_norm:
            break

        # Move up
        parent = os.path.dirname(check_dir)

        # Stop if we hit filesystem root (parent is same as check_dir)
        if parent == check_dir:
            break

        # Stop if we somehow went 'above' root (should only happen if file wasn't inside root)
        if len(parent) < len(root_directory):
             # Double check if we are really outside root
             if not os.path.normcase(check_dir).startswith(root_norm):
                 break

        check_dir = parent

    return ""

def get_info_model_hash(info_path):
    """從 info 檔案讀取 local_sha256 快取值"""
    with open(info_path, 'r', encoding='utf-8') as f:
        local_info = json.load(f)
        extensions = local_info.get("extensions", {})
        if extensions:
            local_sha256 = extensions.get("local_sha256")
            if local_sha256:
                return local_sha256
        files_list = local_info.get("files", [])
        if files_list:
            for file_node in files_list:
                hashes = file_node.get("hashes", {})
                if "SHA256" in hashes:
                    return hashes["SHA256"]

    return None


def scan_directory(directory, refetch_old=False, refetch_only_not_found=False, download_cover=True, max_size_preview=True, download_video=False, attachments_only=False, force_attachments=False):
    """文件掃描主函數 (支援子目錄總結)
    attachments_only=True 時，僅依現有 .civitai.info 重新下載封面/影片附件，
    不重新抓取 API、不改動任何 .civitai.info 檔案。
    force_attachments=True 時，略過「附件已存在」檢查，重新下載以取代舊檔。
    """
    print_log(f"Starting scan in: {directory}", Colors.BLUE)
    if attachments_only:
        print_log("Attachments-only mode: ENABLED (info files untouched)", Colors.MAGENTA)
    if force_attachments:
        print_log("Force attachments: ENABLED (existing attachments re-downloaded)", Colors.MAGENTA)
    if download_cover:
        print_log("Cover download: ENABLED", Colors.CYAN)
    if download_video:
        print_log("Video download: ENABLED", Colors.CYAN)

    global_stats = {
        "processed": 0, "ignored": 0, "updated": 0,
        "not_found": 0, "failed": 0, "duplicates": [], "covers": 0, "videos": 0
    }

    # 使用 mutable list 來讓內嵌函式修改計數
    counter = [0]

    # 哈希緩存: { hash: { 'data': api_info, 'path': first_filepath } }
    hash_cache = {}

    # 記錄已經將 "正本" 加入過 duplicates 清單的 Hash
    recorded_dup_originals = set()

    def process_file(filepath, stats_obj):
        """處理單個文件的邏輯"""
        _, ext = os.path.splitext(filepath)
        if ext not in EXTS:
            return

        counter[0] += 1
        stats_obj["processed"] += 1

        relative_path = os.path.relpath(filepath, start=directory)
        progress_prefix = f"{Colors.GRAY}[{counter[0]}]{Colors.RESET}"

        base, _ = os.path.splitext(filepath)
        info_path = f"{base}{INFO_SUFFIX}"

        # --- Attachments-only: 只重抓附件，絕不碰 info、不重算 hash ---
        if attachments_only:
            if not os.path.exists(info_path):
                return

            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    local_info = json.load(f)
            except Exception:
                return

            # 無效/骨架 info (無 images) 則略過
            if not local_info.get("images"):
                return

            if download_cover:
                try_download_cover(filepath, info_path, stats_obj, progress_prefix, max_size_preview, local_info, force_attachments)
            if download_video:
                download_video_file(filepath, local_info, stats_obj, progress_prefix, force_attachments)
            return

        # 0. 嘗試從現有元數據讀取 Hash (優化效能)
        model_hash = None

        if not refetch_old and os.path.exists(info_path):
            try:
                model_hash = get_info_model_hash(info_path)
            except Exception:
                pass

        # 1. 如果沒讀到或者需要重算，則計算 Hash
        if not model_hash:
            model_hash = calculate_sha256(filepath)

        if not model_hash:
            print_log(f"{progress_prefix} Failed hash: {relative_path}", Colors.RED)
            stats_obj["failed"] += 1
            return

        is_duplicate = False
        info = None

        # 2. Check Cache
        if model_hash in hash_cache:
            is_duplicate = True
            cached_entry = hash_cache[model_hash]
            original_path_abs = cached_entry['path']
            original_path = os.path.relpath(original_path_abs, start=directory)

            print_log(f"{progress_prefix} Duplicate detected (Same as {original_path})", Colors.GRAY)

            # Record duplicate with label
            if model_hash not in recorded_dup_originals:
                # 將正本加入清單
                stats_obj["duplicates"].append((original_path_abs, model_hash))
                recorded_dup_originals.add(model_hash)

            # 將當前副本加入清單
            stats_obj["duplicates"].append((filepath, model_hash))

            info = cached_entry['data']
            if isinstance(info, dict):
                info = info.copy()

        else:
            # First time seeing this hash
            # Initial entry does NOT calculate link type to save performance (Lazy)
            hash_cache[model_hash] = {
                'data': None,
                'path': filepath,
            }

        # Check if metadata update is needed
        need_update = metadata_needed(filepath, refetch_old, refetch_only_not_found)

        if not need_update:
            if not is_duplicate:
                stats_obj["ignored"] += 1

            # 即使不需要更新元數據，仍可下載封面圖 / 影片
            if (download_cover or download_video) and not is_duplicate:
                local_info = None
                if os.path.exists(info_path):
                    try:
                        with open(info_path, 'r', encoding='utf-8') as f:
                            local_info = json.load(f)
                    except Exception:
                        local_info = None

                if download_cover:
                    try_download_cover(filepath, info_path, stats_obj, progress_prefix, max_size_preview, local_info, force_attachments)
                if download_video and local_info:
                    download_video_file(filepath, local_info, stats_obj, progress_prefix, force_attachments)
            return

        # --- If execution reaches here, we DO need to write/update metadata ---

        # If duplicate but missing info (lazy load)
        if is_duplicate and info is None:
            time.sleep(DELAY)
            info = get_model_info_from_civitai(model_hash)
            hash_cache[model_hash]['data'] = info

        # New file needing update
        elif not is_duplicate:
            time.sleep(DELAY)
            info = get_model_info_from_civitai(model_hash)
            hash_cache[model_hash]['data'] = info

        print_log(f"{progress_prefix} Processing: {Colors.CYAN}{relative_path}", Colors.RESET)

        is_skeleton = False
        if not info:
            if not is_duplicate:
                 print_log(f"Model not found: {relative_path}", Colors.YELLOW)

            info = create_skeleton_info(filepath, model_hash)
            is_skeleton = True
        else:
            model_name = info.get('model', {}).get('name', 'Unknown')
            if not is_duplicate:
                print_log(f"Found info: {Colors.GREEN}{model_name}", Colors.RESET)

        info = process_model_info(info, is_skeleton, model_hash)

        if save_info_file(filepath, info):
            if is_duplicate:
                # Already added to list above
                pass
            elif is_skeleton:
                stats_obj["not_found"] += 1
            else:
                stats_obj["updated"] += 1

            # 下載封面圖
            if download_cover and not is_skeleton:
                try_download_cover(filepath, info_path, stats_obj, progress_prefix, max_size_preview, info, force_attachments)

            # 下載影片附件 (選擇性，只取第一部)
            if download_video and not is_skeleton:
                download_video_file(filepath, info, stats_obj, progress_prefix, force_attachments)
        else:
            stats_obj["failed"] += 1

    # 1. 處理根目錄下的文件 (非遞歸)
    try:
        root_items = os.listdir(directory)
    except OSError as e:
        print_log(f"Error listing directory: {e}", Colors.RED)
        return

    root_files = [os.path.join(directory, f) for f in root_items
                  if os.path.isfile(os.path.join(directory, f))]

    for f in root_files:
        process_file(f, global_stats)

    # 2. 處理第一級子目錄
    subdirs = [d for d in root_items
               if os.path.isdir(os.path.join(directory, d))]

    subdirs.sort(key=str.lower)

    for subdir in subdirs:
        subdir_path = os.path.join(directory, subdir)

        print_log(f"Scanning subdirectory: {subdir}...", Colors.BLUE)

        subdir_stats = {
            "processed": 0, "ignored": 0, "updated": 0,
            "not_found": 0, "failed": 0, "duplicates": [], "covers": 0, "videos": 0
        }

        # 對該子目錄進行遞歸掃描 (啟用 followlinks 以進入 junction)
        for root, _, files in os.walk(subdir_path):
            for file in files:
                filepath = os.path.join(root, file)
                process_file(filepath, subdir_stats)

        # 將子目錄統計合併到全域 (duplicates extend list)
        for k in global_stats:
            if k == "duplicates":
                global_stats[k].extend(subdir_stats[k])
            else:
                global_stats[k] += subdir_stats[k]

        # 若有更新檔案(updated, not_found, duplicates)
        if (subdir_stats["updated"] > 0 or
            subdir_stats["not_found"] > 0 or
            len(subdir_stats["duplicates"]) > 0):
            print_summary(subdir, subdir_stats, directory)

    # 最終全域總結
    print_summary(f"Final Count ({directory})", global_stats, directory)

def main():
    global CIVITAI_API_BASE

    # 從環境變數讀取預設值
    env_api_base = os.environ.get("CIVITAI_HELPER_API_BASE", CIVITAI_API_BASE)
    env_no_cover = os.environ.get("CIVITAI_HELPER_NO_COVER", "").lower() in ("1", "true", "yes")
    env_refetch = os.environ.get("CIVITAI_HELPER_REFETCH", "").lower() in ("1", "true", "yes")
    env_refetch_not_found = os.environ.get("CIVITAI_HELPER_REFETCH_ONLY_NOT_FOUND", "").lower() in ("1", "true", "yes")
    env_download_video = os.environ.get("CIVITAI_HELPER_DOWNLOAD_VIDEO", "").lower() in ("1", "true", "yes")
    env_attachments_only = os.environ.get("CIVITAI_HELPER_ATTACHMENTS_ONLY", "").lower() in ("1", "true", "yes")
    env_force_attachments = os.environ.get("CIVITAI_HELPER_FORCE_ATTACHMENTS", "").lower() in ("1", "true", "yes")

    parser = argparse.ArgumentParser(
        description="Civitai Model Scanner CLI - Scan model directory and generate/update .civitai.info metadata",
        epilog="""Environment variables (used when not specified on command line, value: 1/true/yes):
  CIVITAI_HELPER_API_BASE                 -> --api-base
  CIVITAI_HELPER_REFETCH                  -> --refetch
  CIVITAI_HELPER_REFETCH_ONLY_NOT_FOUND   -> --refetch-only-not-found
  CIVITAI_HELPER_NO_COVER                 -> --no-cover
  CIVITAI_HELPER_DOWNLOAD_VIDEO           -> --download-video
  CIVITAI_HELPER_ATTACHMENTS_ONLY         -> --attachments-only
  CIVITAI_HELPER_FORCE_ATTACHMENTS        -> --force-attachments

Examples:
  civitai_scanner.bat "D:\\sd-forge\\models\\Stable-diffusion"
  civitai_scanner.bat "D:\\models" --attachments-only
  civitai_scanner.bat "D:\\models" --attachments-only --force-attachments
  civitai_scanner.bat "D:\\models" --attachments-only --download-video
""",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("path", help="Directory to scan (subdirectories included)")
    parser.add_argument("--refetch", action="store_true", default=env_refetch, help="Force re-fetch all model metadata and rewrite .civitai.info")

    parser.add_argument("--refetch-only-not-found", action="store_true", default=env_refetch_not_found, help="Only re-fetch metadata for models not found previously (skeleton)")

    parser.add_argument("--no-cover", action="store_true", default=env_no_cover, help="Skip downloading model cover preview images")

    parser.add_argument("--download-video", action="store_true", default=env_download_video, help="Also download the first video attachment (saved as {model}.preview.mp4)")

    parser.add_argument("--attachments-only", action="store_true", default=env_attachments_only, help="Only re-download missing attachments from existing .civitai.info; no API calls, info files untouched")

    parser.add_argument("--force-attachments", action="store_true", default=env_force_attachments, help="Ignore existing attachments and re-download to replace possibly corrupt files")

    parser.add_argument("--api-base", default=env_api_base, help=f"Civitai API base URL (default: {env_api_base})")

    args = parser.parse_args()

    # 更新 API Base URL
    CIVITAI_API_BASE = args.api_base.rstrip('/')

    target_path = os.path.abspath(args.path)
    if not os.path.exists(target_path):
        print(f"Error: Path does not exist: {target_path}")
        sys.exit(1)

    try:
        scan_directory(target_path, refetch_old=args.refetch, refetch_only_not_found=args.refetch_only_not_found, download_cover=not args.no_cover, download_video=args.download_video, attachments_only=args.attachments_only, force_attachments=args.force_attachments)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()
