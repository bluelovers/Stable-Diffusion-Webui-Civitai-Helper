# scan_model 核心邏輯深入分析

本文件對 `ch_lib/model_action_civitai.py` 中的 `scan_model` 及其相關函數進行核心邏輯與原理的解讀。

## 1. 函數職責與架構

`scan_model` 是 Civitai Helper 插件中負責"掃描本地模型並獲取 Civitai 資訊"的入口函數。它的設計採用了 **生成器 (Generator)** 模式，這在 Gradio 應用開發中非常常見，目的是為了能夠即時向前端 UI 推送進度更新 (Progress Bar) 和日誌訊息，而不會阻塞主線程等待整個任務完成。

### 主要組成部分
*   **`scan_model`**: 主調度器。負責接收參數、遍歷文件系統、維護總進度，並調用處理函數。
*   **`scan_single_model`**: 核心工作單元。負責處理單一文件的具體邏輯（計算哈希 -> 查詢 API -> 保存數據）。
*   **依靠的外部模組**: `model.py` (路徑與元數據管理), `civitai.py` (API 交互), `util.py` (工具函數)。

## 2. 核心邏輯流程

### 第一階段：發現與枚舉 (Enumeration)
函數首先根據用戶選擇的 `scan_model_types` (如 Checkpoint, LoRA, TI 等) 確定掃描範圍。
*   利用 `os.walk` 遞歸進入模型目錄。
*   通過 `model.EXTS` 過濾掉非模型文件 (如 `.txt`, `.png` 等，只保留 `.safetensors`, `.ckpt` 等)。
*   生成一個待處理的任務列表 `models`。

### 第二階段：哈希計算與識別 (Identification)
這是最核心也最耗時的部分。Civitai 識別模型的唯一可靠方式是 **SHA256 哈希值**，而非文件名。
*   **哈希算法**: 默認計算完整文件的 SHA256。但也支持配置 `ch_autov3` (AutoV3)，僅計算前 12 位字節的哈希，以大幅提高大型模型 (如 SDXL Checkpoints) 的掃描速度。
*   **緩存檢查**: 在計算哈希前，會調用 `model.metadata_needed` 檢查本地是否已經存在對應的 `.civitai.info` 或 WebUI `.json` 文件。如果已存在且版本夠新，則直接跳過，極大節省時間。

### 第三階段：API 交互與數據同步 (Synchronization)
拿到哈希值後，插件向 Civitai API 發起請求。
*   **命中 (Hit)**: 如果 API 返回模型數據，將這些數據 (描述、作者、觸發詞、版本信息) 格式化。
*   **未命中 (Miss)**: 如果 Civitai 上沒有該模型 (或是私有模型)，插件會生成一個 **Dummy Info**。這很重要，因為它標記該文件"已被掃描過"，防止下次掃描時重複計算其哈希值。
*   **防護機制**: 每次請求後執行 `time.sleep(delay)`，這是為了避免觸發 Civitai 伺服器的 Rate Limiting 或 DDoS 防護。

### 第四階段：元數據寫入與模型整理 (Persistance & Organization)
獲取的數據會被寫入硬碟：
1.  **`.civitai.info`**: 插件專用的完整元數據文件。
2.  **`.json`**: SD WebUI 原生支持的元數據格式，這使得用戶在 WebUI 的模型卡片上能看到觸發詞和封面。
3.  **模型整理**: 如果開啟 `organize_models`，會根據 API 返回的標籤 (如 `Style`, `Character`) 自動將 LoRA 模型移動到對應的子文件夾中，保持目錄整潔。

### 第五階段：預覽圖處理 (Assets)
在元數據準備好後，最後一步是檢查預覽圖。
*   調用 `civitai.get_preview_image_by_model_path`。
*   根據用戶設定的 **NSFW 閾值** 和 **最大圖片尺寸** 下載封面圖。

## 3. 代碼邏輯亮點

### 1. 生成器模式的運用
```python
for result in scan_single_model(...):
    if isinstance(result, tuple):
        # 處理哈希計算進度
        progress(...)
```
`scan_single_model` 內部也使用 `yield`。這使得哈希計算這種 CPU 密集型操作的進度也能實時透傳到 UI，避免用戶覺得軟體"卡死"。

### 2. 虛擬元數據 (Dummy Info) 策略
對於無法在 Civitai 找到的模型，代碼選擇創建一個本地的虛擬元數據 (Dummy)。這是一個顯著的優化策略：
*   **問題**: 如果不存任何東西，下次掃描時，程序會認為該模型"未掃描"，再次讀取幾 GB 的文件計算哈希，浪費大量時間。
*   **解決**: 存一個佔位符，標記"已檢查過，此模型不在 Civitai 上"。

### 3. 多模態元數據支持
同時維護自身格式與 WebUI 格式，確保了插件功能的獨立性，同時增強了 WebUI 原生體驗 (如自動補全觸發詞功能依賴於 `.json` 文件)。

## 4. 總結
`scan_model` 是一個強健的同步函數，它解決了"本地文件與雲端數據關聯"的問題。其設計兼顧了 **準確性** (基於哈希)、**效率** (緩存與 Dummy 機制) 和 **用戶體驗** (流式進度回報)。
