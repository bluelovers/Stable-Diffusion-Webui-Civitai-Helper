#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Civitai Model Report Generator
將掃描到的 .civitai.info 元數據文件整合為一個 Markdown 報告。
"""

import os
import json
import hashlib
import argparse
from datetime import datetime

# 配置
INFO_SUFFIX = ".civitai.info"
DEFAULT_REPORT_NAME = "CIVITAI_MODELS_REPORT.md"
MODEL_EXTS = {".bin", ".pt", ".safetensors", ".ckpt", ".gguf", ".zip"}

def format_trigger_words(words):
    if not words:
        return "None"
    return ", ".join([f"`{w}`" for w in words])

def format_tags(tags):
    if not tags:
        return ""
    return " ".join([f"`#{t}`" for t in tags])

def get_preview_paths(info_path, report_root):
    """檢查模型的封面圖 / 封面影片是否存在
    專案慣例檔名: {模型名}.preview.png / {模型名}.preview.mp4
    返回: (封面圖相對報告根目錄路徑 or None, 封面影片相對路徑 or None)
    """
    # 去掉完整的 .civitai.info (splitext 只會去掉最後的 .info)
    if not info_path.endswith(INFO_SUFFIX):
        return None, None
    base = info_path[:-len(INFO_SUFFIX)]

    cover_image = None
    cover_video = None

    img_path = f"{base}.preview.png"
    if os.path.exists(img_path):
        cover_image = os.path.relpath(img_path, report_root).replace("\\", "/")

    vid_path = f"{base}.preview.mp4"
    if os.path.exists(vid_path):
        cover_video = os.path.relpath(vid_path, report_root).replace("\\", "/")

    return cover_image, cover_video

def get_model_path(info_path):
    """由 .civitai.info 路徑推得同名的本地模型檔路徑
    info 檔名 = {模型 base}.civitai.info，模型檔 = {模型 base}.{模型副檔名}
    回傳模型檔路徑，找不到時回傳 None
    """
    if not info_path.endswith(INFO_SUFFIX):
        return None
    base = info_path[:-len(INFO_SUFFIX)]

    for ext in MODEL_EXTS:
        candidate = f"{base}{ext}"
        if os.path.exists(candidate):
            return candidate
    return None

def compute_sha256(file_path, chunk_size=1024 * 1024):
    """讀檔重算 SHA256 (1MB 分塊)，回傳大寫 hex，失敗回傳 None"""
    try:
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(chunk_size), b""):
                h.update(block)
        return h.hexdigest().upper()
    except OSError:
        return None

def get_sha256(info_path, show_progress=False):
    """取得本地模型檔的 SHA256
    直接重算本地模型檔內容最準確 —— 檔名不保證等於 info 內 files[].name，
    且同一版本可能有多個 files (不同量化)。info 僅作為找不到模型檔時的最後 fallback。
    show_progress=True 時印出正在計算的檔名 (大檔重算耗時，避免看似卡住)。
    回傳大寫 hex，無法取得時回傳 None
    """
    model_path = get_model_path(info_path)
    if model_path:
        if show_progress:
            size_mb = os.path.getsize(model_path) / (1024 * 1024)
            print(f"  Hashing ({size_mb:,.0f} MB): {os.path.basename(model_path)}...")
        sha = compute_sha256(model_path)
        if sha:
            return sha

    # Fallback: 找不到本地模型檔時，退回 info 內單一檔案的 hash
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        files = info.get("files", [])
        if len(files) == 1:
            return (files[0].get("hashes", {}).get("SHA256") or "").upper() or None
    except Exception:
        pass
    return None

def generate_report(directory, output_file):
    print(f"Scanning for {INFO_SUFFIX} files in: {directory}...")
    
    models_by_type = {}
    total_count = 0
    skeleton_count = 0
    
    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(INFO_SUFFIX):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        info = json.load(f)
                    
                    # 獲取模型資訊
                    m_model = info.get("model", {})
                    m_type = m_model.get("type", "Unknown")
                    m_name = m_model.get("name") or info.get("name") or "Unnamed Model"
                    v_name = info.get("name", "Unknown Version")
                    
                    is_skeleton = info.get("skeleton_file", False)
                    if not is_skeleton:
                        exts = info.get("extensions", {})
                        if exts.get("sd_civitai_helper", {}).get("skeleton_file", False):
                            is_skeleton = True
                    
                    if is_skeleton:
                        skeleton_count += 1
                    
                    if m_type not in models_by_type:
                        models_by_type[m_type] = []
                    
                    # 封面圖 / 封面影片 (連結路徑相對於報告根目錄)
                    cover_image, cover_video = get_preview_paths(file_path, directory)
                    
                    # 組合顯示需要的數據
                    model_data = {
                        "name": m_name,
                        "version": v_name,
                        "type": m_type,
                        "baseModel": info.get("baseModel", "Unknown"),
                        "trainedWords": info.get("trainedWords", []),
                        "tags": info.get("tags", []),
                        "modelId": info.get("modelId"),
                        "versionId": info.get("id"),
                        "is_skeleton": is_skeleton,
                        "file_rel_path": os.path.relpath(file_path, directory),
                        "cover_image": cover_image,
                        "cover_video": cover_video,
                        "sha256": get_sha256(file_path, show_progress=True)
                    }
                    
                    models_by_type[m_type].append(model_data)
                    total_count += 1
                    
                except Exception as e:
                    print(f"Error reading {file_path}: {e}")

    if total_count == 0:
        print("No .civitai.info files found.")
        return

    # 排序
    for m_type in models_by_type:
        models_by_type[m_type].sort(key=lambda x: x["name"].lower())

    # 開始寫入 Markdown
    with open(output_file, 'w', encoding='utf-8') as md:
        md.write(f"# Civitai Models Report\n\n")
        md.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
        
        # 總結
        md.write(f"## Summary\n\n")
        md.write(f"- **Total Models Detected:** {total_count}\n")
        md.write(f"- **Linked to Civitai:** {total_count - skeleton_count}\n")
        md.write(f"- **Local Only (Not Found):** {skeleton_count}\n\n")
        
        md.write("| Type | Count |\n")
        md.write("| :--- | :---- |\n")
        for m_type in sorted(models_by_type.keys()):
            md.write(f"| {m_type} | {len(models_by_type[m_type])} |\n")
        md.write("\n---\n\n")

        # 詳細列表
        for m_type in sorted(models_by_type.keys()):
            md.write(f"## {m_type}\n\n")
            
            for m in models_by_type[m_type]:
                status_tag = " ⚠️ (Not on Civitai)" if m["is_skeleton"] else ""
                md.write(f"### {m['name']} - {m['version']}{status_tag}\n\n")
                
                md.write(f"- **Base Model:** {m['baseModel']}\n")
                
                if m['trainedWords']:
                    md.write(f"- **Trigger Words:** {format_trigger_words(m['trainedWords'])}\n")
                
                if m['tags']:
                    md.write(f"- **Tags:** {format_tags(m['tags'])}\n")
                
                if not m['is_skeleton'] and m['modelId']:
                    civitai_link = f"https://civitai.com/models/{m['modelId']}"
                    if m['versionId']:
                        civitai_link += f"?modelVersionId={m['versionId']}"
                    md.write(f"- **Link:** [View on Civitai]({civitai_link})\n")
                
                md.write(f"- **Local Path:** `{m['file_rel_path']}`\n")

                if m['cover_image']:
                    md.write(f"- **Cover Image:** [{os.path.basename(m['cover_image'])}]({m['cover_image']})\n")

                if m['cover_video']:
                    md.write(f"- **Cover Video:** [{os.path.basename(m['cover_video'])}]({m['cover_video']})\n")

                md.write("\n")
            
            md.write("---\n\n")

        # 重複檔案報告 (依 SHA256 比對)
        write_duplicate_report(md, models_by_type)

    print(f"Report generated successfully: {output_file}")

def write_duplicate_report(md, models_by_type):
    """依 SHA256 群組，列出內容重複的模型檔案"""
    # 攤平所有模型並以 sha256 群組
    all_models = []
    for m_type in models_by_type:
        all_models.extend(models_by_type[m_type])

    hash_groups = {}
    for m in all_models:
        sha = m.get("sha256")
        if not sha:
            continue
        hash_groups.setdefault(sha, []).append(m)

    # 只保留出現兩次以上的
    dup_groups = {sha: group for sha, group in hash_groups.items() if len(group) > 1}

    md.write("## Duplicate Files\n\n")

    if not dup_groups:
        md.write("No duplicate model files detected.\n\n")
        return

    dup_file_count = sum(len(group) for group in dup_groups.values())
    md.write(f"- **Duplicate Groups:** {len(dup_groups)}\n")
    md.write(f"- **Files Involved:** {dup_file_count}\n\n")

    for i, (sha, group) in enumerate(sorted(dup_groups.items(), key=lambda x: x[1][0]["name"].lower()), start=1):
        first = group[0]
        md.write(f"### Group {i}: {first['name']} - {first['version']}\n\n")
        md.write(f"- **SHA256:** `{sha}`\n")
        md.write(f"- **Copies:** {len(group)}\n\n")
        md.write("| # | Type | Version | Local Path |\n")
        md.write("| :--- | :--- | :--- | :--- |\n")
        for j, m in enumerate(group, start=1):
            md.write(f"| {j} | {m['type']} | {m['version']} | `{m['file_rel_path']}` |\n")
        md.write("\n")

    md.write("---\n\n")

def main():
    parser = argparse.ArgumentParser(description="Generate a Markdown report from .civitai.info files.")
    parser.add_argument("path", help="Directory to scan")
    parser.add_argument("-o", "--output", help="Output Markdown file name", default=DEFAULT_REPORT_NAME)
    
    args = parser.parse_args()
    
    target_path = os.path.abspath(args.path)
    if not os.path.exists(target_path):
        print(f"Error: Path does not exist: {target_path}")
        return
        
    output_path = os.path.join(target_path, args.output)
    
    generate_report(target_path, output_path)

if __name__ == "__main__":
    main()
