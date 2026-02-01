#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Civitai Model Scanner CLI
基於 Stable-Diffusion-Webui-Civitai-Helper 的 scan_model 邏輯。
獨立運行的命令行工具，無需 WebUI 介面。
僅生成/更新 .civitai.info 元數據文件。

依賴: requests
安裝: pip install requests
"""

import os
import stat
import sys
import json
import time
import hashlib
import argparse
import requests
from datetime import datetime
from functools import lru_cache

# 配置
CIVITAI_API_URL = "https://civitai.com/api/v1/model-versions/by-hash/"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
EXTS = {".bin", ".pt", ".safetensors", ".ckpt", ".gguf", ".zip"}
INFO_SUFFIX = ".civitai.info"
SHORT_NAME = "sd_civitai_helper"
VERSION = "1.8.13"  # 保持與主專案一致或自定義
DELAY = 0.5  # API 請求間隔 (秒)

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
    CYAN = "\033[96m" if USE_COLORS else ""
    GRAY = "\033[90m" if USE_COLORS else ""

def print_log(msg, color=None):
    timestamp = datetime.now().strftime("%H:%M:%S")
    timestamp_str = f"{Colors.GRAY}[{timestamp}]{Colors.RESET}"
    
    if color:
        print(f"{timestamp_str} {color}{msg}{Colors.RESET}")
    else:
        print(f"{timestamp_str} {msg}")

def calculate_sha256(filepath):
    """計算文件的 SHA256 哈希值"""
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
    url = f"{CIVITAI_API_URL}{model_hash}"
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

def process_model_info(info, is_skeleton=False):
    """處理模型資訊，添加插件擴展字段"""
    # 確保 extensions 字段存在
    if "extensions" not in info:
        info["extensions"] = {}
    
    # 添加 sd_civitai_helper 版本資訊
    info["extensions"][SHORT_NAME] = {
        "version": VERSION,
        "last_update": int(time.time()),
        "skeleton_file": is_skeleton
    }
    return info

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

def metadata_needed(filepath, refetch_old):
    """檢查是否需要掃描"""
    base, _ = os.path.splitext(filepath)
    info_path = f"{base}{INFO_SUFFIX}"
    
    if not os.path.exists(info_path):
        return True
    
    if refetch_old:
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

def scan_directory(directory, refetch_old=False):
    """文件掃描主函數 (支援子目錄總結)"""
    print_log(f"Starting scan in: {directory}", Colors.BLUE)
    
    global_stats = {
        "processed": 0, "ignored": 0, "updated": 0, 
        "not_found": 0, "failed": 0, "duplicates": []
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
        
        
        # 0. 嘗試從現有元數據讀取 Hash (優化效能)
        model_hash = None
        base, _ = os.path.splitext(filepath)
        info_path = f"{base}{INFO_SUFFIX}"
        
        if not refetch_old and os.path.exists(info_path):
            try:
                with open(info_path, 'r', encoding='utf-8') as f:
                    local_info = json.load(f)
                    files_list = local_info.get("files", [])
                    if files_list:
                        for file_node in files_list:
                            hashes = file_node.get("hashes", {})
                            if "SHA256" in hashes:
                                model_hash = hashes["SHA256"]
                                break
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
        # 注意: 即使我們剛從 info 讀了 hash，這裡如果是 True (例如 user delete .json but keep .info?)
        # 其實 metadata_needed checks checks .info existence.
        # 如果是 not refetch_old, and info exists -> need_update is False.
        # 但如果 info 讀不到 hash (corrupted?), we calculated new hash.
        # Still, logic should be consistent.
        need_update = metadata_needed(filepath, refetch_old)

        if not need_update:
            if not is_duplicate:
                stats_obj["ignored"] += 1
            # If it IS a duplicate, we already counted it in duplicates list.
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
        
        info = process_model_info(info, is_skeleton)
        
        if save_info_file(filepath, info):
            if is_duplicate:
                # Already added to list above
                pass
            elif is_skeleton:
                stats_obj["not_found"] += 1
            else:
                stats_obj["updated"] += 1
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
            "not_found": 0, "failed": 0, "duplicates": []
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
    parser = argparse.ArgumentParser(description="Civitai Model Scanner CLI")
    parser.add_argument("path", help="要掃描的目錄路徑")
    parser.add_argument("--refetch", action="store_true", help="強制重新獲取已此存在的元數據")
    
    args = parser.parse_args()
    
    target_path = os.path.abspath(args.path)
    if not os.path.exists(target_path):
        print(f"Error: Path does not exist: {target_path}")
        sys.exit(1)
        
    try:
        scan_directory(target_path, refetch_old=args.refetch)
    except KeyboardInterrupt:
        print("\nOperation cancelled by user.")
        sys.exit(0)

if __name__ == "__main__":
    main()
