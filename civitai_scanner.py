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
import sys
import json
import time
import hashlib
import argparse
import requests
from datetime import datetime

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

def print_summary(title, stats):
    """Helper to print stats summary"""
    border = f"{Colors.GRAY}{'-'*50}{Colors.RESET}"
    print("\n" + border)
    print(f"{Colors.BOLD}Summary for: {Colors.CYAN}{title}{Colors.RESET}")
    print(f"Files Scanned: {stats['processed']}")
    print(f"Ignored:       {Colors.GRAY}{stats['ignored']}{Colors.RESET}")
    
    # Updated (Green if > 0)
    updated_color = Colors.GREEN if stats['updated'] > 0 else ""
    print(f"Updated:       {updated_color}{stats['updated']}{Colors.RESET}")
    
    # Not Found (Yellow if > 0)
    nf_color = Colors.YELLOW if stats['not_found'] > 0 else ""
    print(f"Not Found:     {nf_color}{stats['not_found']}{Colors.RESET}")
    
    # Failed (Red if > 0)
    if stats['failed'] > 0:
        print(f"Failed:        {Colors.RED}{stats['failed']}{Colors.RESET}")
        
    print(border + "\n")

def scan_directory(directory, refetch_old=False):
    """文件掃描主函數 (支援子目錄總結)"""
    print_log(f"Starting scan in: {directory}", Colors.BLUE)
    
    global_stats = {
        "processed": 0, "ignored": 0, "updated": 0, "not_found": 0, "failed": 0
    }
    
    # 使用 mutable list 來讓內嵌函式修改計數
    counter = [0] 

    def process_file(filepath, stats_obj):
        """處理單個文件的邏輯"""
        _, ext = os.path.splitext(filepath)
        if ext not in EXTS:
            return

        counter[0] += 1
        stats_obj["processed"] += 1
        
        # 關鍵修改：確保這裡顯示的相對路徑是基於最上層的 directory
        relative_path = os.path.relpath(filepath, start=directory)
        progress_prefix = f"{Colors.GRAY}[{counter[0]}]{Colors.RESET}"

        if not metadata_needed(filepath, refetch_old):
            stats_obj["ignored"] += 1
            return
            
        print_log(f"{progress_prefix} Processing: {Colors.CYAN}{relative_path}", Colors.RESET)
        
        # 1. Hash
        model_hash = calculate_sha256(filepath)
        if not model_hash:
            print_log(f"{progress_prefix} Failed hash: {relative_path}", Colors.RED)
            stats_obj["failed"] += 1
            return
            
        # 2. API
        time.sleep(DELAY)
        info = get_model_info_from_civitai(model_hash)
        
        is_skeleton = False
        if not info:
            print_log(f"Model not found: {relative_path}", Colors.YELLOW)
            info = create_skeleton_info(filepath, model_hash)
            is_skeleton = True
        else:
            model_name = info.get('model', {}).get('name', 'Unknown')
            print_log(f"Found info: {Colors.GREEN}{model_name}", Colors.RESET)
        
        # 3. Process & Save
        info = process_model_info(info, is_skeleton)
        
        if save_info_file(filepath, info):
            if is_skeleton:
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
    
    subdirs.sort()

    for subdir in subdirs:
        subdir_path = os.path.join(directory, subdir)
        
        print_log(f"Scanning subdirectory: {subdir}...", Colors.BLUE)
        
        subdir_stats = {
            "processed": 0, "ignored": 0, "updated": 0, "not_found": 0, "failed": 0
        }
        
        # 對該子目錄進行遞歸掃描
        for root, _, files in os.walk(subdir_path):
            for file in files:
                filepath = os.path.join(root, file)
                process_file(filepath, subdir_stats)
        
        # 將子目錄統計合併到全域
        for k in global_stats:
            global_stats[k] += subdir_stats[k]
            
        # 若有更新檔案，顯示該子目錄的總結
        if subdir_stats["updated"] > 0 or subdir_stats["not_found"] > 0:
            print_summary(subdir, subdir_stats)

    # 最終全域總結
    print_summary(f"Final Count ({directory})", global_stats)

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
