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

def print_log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

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
        print_log(f"Error calculating hash: {e}")
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
            print_log(f"API Error: Status {response.status_code}")
            return None
    except Exception as e:
        print_log(f"Connection Error: {e}")
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
        print_log(f"Saved metadata: {os.path.basename(info_path)}")
        return True
    except Exception as e:
        print_log(f"Error saving file: {e}")
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

def scan_directory(directory, refetch_old=False):
    """遞歸掃描目錄"""
    print_log(f"Starting scan in: {directory}")
    
    count_total = 0
    count_processed = 0
    
    for root, _, files in os.walk(directory):
        for file in files:
            filepath = os.path.join(root, file)
            _, ext = os.path.splitext(file)
            
            if ext not in EXTS:
                continue
                
            count_total += 1
            
            if not metadata_needed(filepath, refetch_old):
                continue
            
            print_log(f"Processing: {file}")
            
            # 1. 計算 Hash
            model_hash = calculate_sha256(filepath)
            if not model_hash:
                continue
                
            # 2. 查詢 Civitai
            time.sleep(DELAY) # 防 DDoS
            info = get_model_info_from_civitai(model_hash)
            
            is_skeleton = False
            if not info:
                print_log(f"Model not found on Civitai: {file}")
                # 3a. 創建 Dummy Info
                info = create_skeleton_info(filepath, model_hash)
                is_skeleton = True
            else:
                print_log(f"Found model info: {info.get('model', {}).get('name', 'Unknown')}")
            
            # 4. 處理數據
            info = process_model_info(info, is_skeleton)
            
            # 5. 保存文件
            if save_info_file(filepath, info):
                count_processed += 1
                
    print_log(f"Scan complete. Processed {count_processed}/{count_total} models.")

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
