#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Civitai Model Report Generator
將掃描到的 .civitai.info 元數據文件整合為一個 Markdown 報告。
"""

import os
import json
import stat
import hashlib
import argparse
from datetime import datetime
from functools import lru_cache

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

def compute_sha256(file_path):
    """與 civitai_scanner.py calculate_sha256 相同的算法 (SHA256、65536 bytes 分塊、大寫 hex)"""
    sha256_hash = hashlib.sha256()
    block_size = 65536  # 64kb

    try:
        with open(file_path, "rb") as f:
            for block in iter(lambda: f.read(block_size), b""):
                sha256_hash.update(block)
        return sha256_hash.hexdigest().upper()
    except Exception as e:
        print(f"Error calculating hash: {e}")
        return None

def write_local_sha256(info_path, info, sha):
    """將重算的本地 hash 回填到 info 的 extensions.sd_civitai_helper.local_sha256。
    僅在讀取不到快取、由 report 重算成功時呼叫 (report 端的快取回填)。
    只新增此欄位，不動 files[].hashes；骨架 info 不寫入。
    寫入失敗僅提示，不影響報告產出。
    """
    SHORT_NAME = "sd_civitai_helper"

    extensions = None
    is_skeleton = False
    if info is None:
        is_skeleton = True
        extensions = {
            SHORT_NAME: {
                "skeleton_file": True
            }
        }
        info = {
            "extensions": extensions
        }
    else:
        is_skeleton = info.get("skeleton_file", None)
        extensions = info.get("extensions", None)
        if extensions is None or extensions.get(SHORT_NAME, None) is None:
            extensions = {} if extensions is None else extensions
            extensions[SHORT_NAME] = {
                "skeleton_file": is_skeleton
            }

        if is_skeleton is None:
            is_skeleton = extensions.get(SHORT_NAME, {}).get("skeleton_file", False)

    # info["skeleton_file"] = is_skeleton

    extensions[SHORT_NAME]["skeleton_file"] = is_skeleton

    try:
        extensions["local_sha256"] = sha
        info["extensions"] = extensions

        with open(info_path, "w", encoding="utf-8") as f:
            json.dump(info, f, indent=4, ensure_ascii=False)
        return True
    except Exception as e:
        print(f"Warning: failed to write local_sha256 for {os.path.basename(info_path)}: {e}")
        return False

def get_sha256(info_path, show_progress=False):
    """取得本地模型檔的 SHA256 (用於重複檔案比對)
    1) 優先讀取快取欄位 extensions.sd_civitai_helper.local_sha256
       (scanner 寫入 info 時一併寫入，或 report 重算後回填)；
       讀取得到就直接採用，免重讀大檔 —— report 無法自行得知確實的 hash，
       此快取正是讓 report 免重算的機制。
    2) 讀取不到 (舊版 info / 欄位缺失) 則重算本地模型檔，成功後寫回 info
       回填快取，下次直接讀取 —— 以內容為準，檔名不保證等於 info 內
       files[].name，同一版本也可能有多個 files。
    3) 找不到本地模型檔時，退回 info 內單一檔案的 API hash (不寫回)。
    show_progress=True 時，重算前印出檔名與大小 (大檔耗時，避免看似卡住)。
    回傳大寫 hex，無法取得時回傳 None
    """
    # 讀 info: 供 1) 快取、2) 回填與 3) fallback 共用
    info = None
    try:
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
    except Exception:
        pass

    # 1) 快取值 (scanner 寫入或 report 回填) —— 讀取得到就免重算
    if info:
        sha = info.get("extensions", {}).get("local_sha256")
        if sha:
            return sha.upper()

    # 2) 讀取不到 → 重算本地模型檔，成功後寫回 info 供下次直接讀取
    model_path = get_model_path(info_path)
    if model_path:
        if show_progress:
            size_mb = os.path.getsize(model_path) / (1024 * 1024)
            print(f"  Hashing ({size_mb:,.0f} MB): {os.path.basename(model_path)}...")
        sha = compute_sha256(model_path)
        if sha:
            write_local_sha256(info_path, info, sha)
            return sha

    # 3) Fallback: 找不到本地模型檔時，退回 info 內單一檔案的 hash (不寫回)
    if info:
        files = info.get("files", [])
        if len(files) == 1:
            return (files[0].get("hashes", {}).get("SHA256") or "").upper() or None
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
                        "file_abs_path": file_path,
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
        write_duplicate_report(md, models_by_type, directory)

    print(f"Report generated successfully: {output_file}")

@lru_cache(maxsize=None)
def is_junction_or_link(path):
    """偵測路徑本身的連結型別 (移植自 civitai_scanner.py，同一套判斷)
    Windows 上用 os.lstat() 搭配 stat.FILE_ATTRIBUTE_REPARSE_POINT
    先判斷是否為 reparse point，再用 os.path.islink() 區分 symlink；
    是 reparse point 但不是 symlink，那麼很可能就是 junction。
    檔案 (非目錄) 則以 st.st_nlink > 1 判斷 hardlink。
    回傳: "Symlink" / "Junction" / "Hardlink" / "" (一般路徑) / None (偵測失敗)
    """
    link_type = ""
    try:
        if os.path.islink(path):
            link_type = "Symlink"
        else:
            is_dir = os.path.isdir(path)

            st = os.lstat(path)
            # st_file_attributes 僅 Windows 存在，其他平台視為 0 (非 reparse point)
            if is_dir and getattr(st, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
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
    """(Lazy) 回傳檔案路徑鏈上的連結型別標註 (含父目錄 junction 檢查)
    與 civitai_scanner.py get_link_type 相同邏輯 (lru_cache 緩存、
    由檔案本身往上逐層檢查父目錄直到 root)；差別僅在回傳 Markdown
    用的純文字而非終端機顏色字串。
    回傳: " (Symlink)" / " (Junction)" / " (Hardlink)" / ""
    """
    try:
        filepath = os.path.abspath(filepath)
        root_directory = os.path.abspath(root_directory)
    except:
        return ""

    # Check file itself
    link_type = is_junction_or_link(filepath)
    if link_type == "Symlink" or link_type == "Hardlink":
        return f" ({link_type})"

    # Junction/Link check in parents
    check_dir = os.path.dirname(filepath)
    root_norm = os.path.normcase(root_directory)

    while True:
        # Check if current dir is a link/junction
        ptype = is_junction_or_link(check_dir)
        if ptype:
            return f" ({ptype})"

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

def write_duplicate_report(md, models_by_type, report_root):
    """依 SHA256 群組，列出內容重複的模型檔案
    每個項目附連結型別 (Symlink/Junction/Hardlink，偵測邏輯同 civitai_scanner.py)，
    讀者可分辨「真正多佔空間的實體副本」與「經連結指向同一實體檔的項目」。
    """
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

    # 先為所有項目偵測連結型別 (lazy + lru_cache，同 scanner 的時機: 只在報告階段才做 I/O)
    # 並把「一般檔案」排在前、連結項目排在後 —— 群組第一列即為正本/實體副本
    for group in dup_groups.values():
        for m in group:
            m["link_type"] = get_link_type(m["file_abs_path"], report_root).strip()
        group.sort(key=lambda x: x["link_type"] != "")

    dup_file_count = sum(len(group) for group in dup_groups.values())
    link_file_count = sum(1 for group in dup_groups.values() for m in group if m["link_type"])

    md.write(f"- **Duplicate Groups:** {len(dup_groups)}\n")
    md.write(f"- **Files Involved:** {dup_file_count}\n")
    if link_file_count:
        md.write(f"- **Entries via Links:** {link_file_count} (Symlink/Junction/Hardlink)\n")
    md.write("\n")

    for i, (sha, group) in enumerate(sorted(dup_groups.items(), key=lambda x: x[1][0]["name"].lower()), start=1):
        link_count = sum(1 for m in group if m["link_type"])
        first = group[0]
        md.write(f"### Group {i}: {first['name']} - {first['version']}\n\n")
        md.write(f"- **SHA256:** `{sha}`\n")
        copies_line = f"- **Copies:** {len(group)}"
        if link_count:
            copies_line += f" ({link_count} via link)"
        md.write(copies_line + "\n\n")
        md.write("| # | Type | Version | Local Path | Link |\n")
        md.write("| :--- | :--- | :--- | :--- | :--- |\n")
        for j, m in enumerate(group, start=1):
            link_cell = m["link_type"] or "-"
            md.write(f"| {j} | {m['type']} | {m['version']} | `{m['file_rel_path']}` | {link_cell} |\n")
        md.write("\n")

    if link_file_count:
        md.write("> **Note:** Entries marked `Symlink`/`Junction`/`Hardlink` are link-type paths "
                 "(detected the same way as `civitai_scanner.py`); they may point to the same "
                 "physical file and are not true extra copies. Only unmarked entries occupy "
                 "additional disk space.\n\n")

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
