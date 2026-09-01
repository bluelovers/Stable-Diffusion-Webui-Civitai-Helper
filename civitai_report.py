#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Civitai Model Report Generator
將掃描到的 .civitai.info 元數據文件整合為一個 Markdown 報告。
"""

import os
import json
import argparse
from datetime import datetime

# 配置
INFO_SUFFIX = ".civitai.info"
DEFAULT_REPORT_NAME = "CIVITAI_MODELS_REPORT.md"

def format_trigger_words(words):
    if not words:
        return "None"
    return ", ".join([f"`{w}`" for w in words])

def format_tags(tags):
    if not tags:
        return ""
    return " ".join([f"`#{t}`" for t in tags])

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
                        "file_rel_path": os.path.relpath(file_path, directory)
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
                md.write("\n")
            
            md.write("---\n\n")

    print(f"Report generated successfully: {output_file}")

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
