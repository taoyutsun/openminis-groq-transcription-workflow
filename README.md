# Open Minis：手機錄音轉錄與繁中摘要 workflow

繁體中文 | [English](README.en.md)

在 Open Minis 的 Linux 環境執行 Python 腳本，讀取你指定的音檔資料夾，透過 Groq Whisper 產生原語言 SRT 與帶時間戳的逐字稿，再呼叫 App 已授權的模型產生繁體中文 Markdown 摘要。v0.2.1 整合 iOS／Android 模型橋接格式；預設推薦 GPT-6-Sol，也可明確指定自己開放的文字模型。Groq 文字模型保留為明確選擇的備案。

來源可以是 iPhone「語音備忘錄」匯出的檔案、第三方錄音 App 開放的資料夾，或 Android 可存取的錄音資料夾。腳本使用同一套參數，無須寫死錄音 App 名稱或 Android 品牌路徑。

**這是手機執行、雲端辨識與摘要的流程，需要網路及你自己的 Groq API Key。** 音訊片段會上傳 Groq；Minis 摘要將逐字稿文字送往所選模型服務，Groq 摘要則將逐字稿及分段摘要送至 Groq。批次處理是逐檔呼叫一般 API，並非 Groq Batch API，也沒有定時監看資料夾的功能。

長時間處理時，建議讓 Open Minis 保持在前景。iPhone 也可從「設定 → 權限 → 背景」開啟「增強背景運行」，並依 App 提示設定相關選項與權限，協助任務在切換 App 後繼續執行。這項設定不代表任務一定能在鎖屏後完成；若系統掛起 App 或程序被終止，請以相同參數重新執行。介面位置與可用選項以安裝版本為準。官方實作：[iOS 背景設定](https://github.com/OpenMinis/OpenMinis/blob/main/src/ios/Views/Settings/EnhancedBackgroundSettingsView.swift)。

## 測試狀態

本公開版為 `0.2.1`。Windows／Ubuntu WSL 各通過 67 項離線測試，涵蓋雙平台格式、草稿重跑及批次結果狀態。朋友回報修改過的私人 Android 版本可以使用，是本次相容性改善的依據；不代表這份公開版已完成雙平台真機驗證。作者私人 iPhone 版的長錄音成功紀錄另見 [測試範圍](docs/TESTING.md)。

本版附有離線回歸測試與合成 M4A／WAV 音訊測試；測試結果及待驗證項目見 [測試範圍](docs/TESTING.md)。

## 1. 安裝與準備

1. 從 [Open Minis 官方網站](https://openminis.app/) 或 [官方原始碼專案](https://github.com/OpenMinis/OpenMinis) 選取對應平台的安裝入口。

2. 從 [最新正式 Release](https://github.com/taoyutsun/openminis-groq-transcription-workflow/releases/latest) 下載 ZIP 並解壓，將五支程式 `groq_workflow.py`、`groq_transcribe.py`、`groq_http.py`、`groq_summary.py`、`minis_summary.py` 放在同一資料夾。範例安裝位置是 `/var/minis/shared/錄音轉錄工具/`。README 可一併保留。升級時五支程式一起更新，先保留舊程式及原輸出。

3. 在 Open Minis 終端機確認 Python 3.8 以上及 FFmpeg／ffprobe 可執行。在 Alpine 環境中，缺少套件時可安裝：

   ```sh
   apk add python3 ffmpeg ca-certificates
   ```

   若裝置環境禁止安裝套件，請使用該版本 Open Minis 提供的安裝方式。FFmpeg 需能讀取來源格式；M4A／AAC 優先用 FFmpeg。SoX 是可選備援，需自行確認其解碼器支援。

4. 在 [Groq Console](https://console.groq.com/keys) 建立自己的 API Key，於 Open Minis 的環境變數設定加入 `GROQ_API_KEY`。供應商設定內的 Key 不一定自動提供給 shell，請確認腳本環境也有設定。不要把 Key 貼到對話、程式或 GitHub。

5. 在 Open Minis 中完成你要使用的模型服務登入或設定。若用 Codex OAuth 的 GPT-6-Sol，再到「設定 → 模型分組 → Minis 執行時可調用的模型」加入 **GPT-6-Sol**。僅設定聊天的預設主模型不夠；腳本只看這份允許清單。不要讀取、匯出或把 OAuth token 當成 API Key。

6. 建立或掛載音檔來源資料夾，並確認在 Open Minis 終端機內可讀。

先做本機環境檢查：

```sh
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --check
```

它顯示音訊工具、Key 是否設定及 Minis 摘要模型是否在本機允許清單，不顯示 Key，也不驗證帳戶額度或真實模型請求。模型名稱有重複供應商時，用 `--minis-provider '你的供應商標籤'` 明確指定。

若不使用 App 模型橋接，或橋接格式不在本版支援範圍，請明確選用 `--summary-backend groq`；檢查時也加上這個選項。舊的 v0.1.0 使用方式可透過環境變數 `SUMMARY_BACKEND=groq` 保留；新版不會在 Minis 失敗時自動回退。Android 缺少完成狀態的相容回應可另存草稿，詳見「輸出與續跑」。

## 2. iPhone 音檔入口

### Apple「語音備忘錄」：先匯出到專用資料夾

1. 在「語音備忘錄」選取錄音，點「更多」→「分享」→「儲存到檔案」。
2. 選擇單一音檔／已算圖的 M4A 分享格式。可編輯的多音層檔案不在本版測試範圍。
3. 儲存到「檔案」App 可讀的專用 `錄音輸入` 資料夾。若 Open Minis 的共享位置可選，將它放入該位置，確認 Linux 內對應 `/var/minis/shared/錄音輸入/`。
4. 若儲存在其他位置，到 Open Minis 的外部資料夾掛載設定選取它，再記下 App 顯示的 Linux 路徑。例如 `/var/minis/mounts/錄音輸入/`；名稱以你實際掛載的資料夾為準。
5. 輸出使用獨立資料夾，避免和錄音來源混在一起。

「語音備忘錄」內的分類資料夾不是「檔案」App 裡可直接掛載的資料夾。本流程採用 Apple 提供的分享匯出方式，不需要尋找 App 的內部儲存路徑。iCloud 中的檔案請先確保下載到手機、可實際讀取。

Apple 官方：[將語音備忘錄輸出至檔案](https://support.apple.com/zh-tw/guide/iphone/iph831c37815/ios)。

### 第三方錄音 App

若錄音 App 在「檔案」中開放音檔資料夾，可直接掛載並以 `--source` 指定；若未開放，就從錄音 App 分享音檔至上述 `錄音輸入` 資料夾。「錄音機 Pro」只是其中一個例子，不是必要套件。

## 3. Android 音檔入口

先在檔案管理員確認錄音位置。以 Samsung 為例，官方說明中的路徑是「內部儲存空間 → Recordings → Voice Recorder」；其他品牌、系統版本及錄音 App 可能使用不同位置。

到 Open Minis 的外部資料夾掛載設定選取實際的錄音資料夾，授權後使用 App 顯示的 Linux 路徑，例如：

```sh
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --source '/var/minis/mounts/Voice Recorder' --list
```

`/內部儲存空間/Recordings/Voice Recorder` 是檔案管理員的顯示方式，不能直接當成此腳本的 Linux 路徑；也不要假設 `/storage/emulated/0/...` 已在 Open Minis 內可讀。先掛載並列出檔案，確認實際路徑。

如果錄音儲存在 App 私有區、無法選取掛載、雲端供應商不支援直接掛載，或錄音 App 不公開檔案位置，就將音檔分享／匯出到手機本機的專用 `錄音輸入` 資料夾，再掛載該資料夾。這也是跨品牌較容易重複操作的入口。

官方參考：[Samsung 錄音檔案位置與分享](https://www.samsung.com/my/support/mobile-devices/how-to-record-play-back-and-share-voice-recordings-on-your-samsung-galaxy-phone/)、[Android 資料夾存取授權](https://developer.android.com/training/data-storage/shared/documents-files)、[Open Minis Android 掛載介面原始碼](https://github.com/OpenMinis/OpenMinis/blob/main/src/android/app/src/main/java/com/openminis/app/ui/settings/MountedFoldersScreen.kt)。

讀者應先用不含私人資訊的短錄音驗證掛載、解碼及輸出存取。

v0.2.1 可讀取 Android 直接回傳的模型清單與 `model`／`text` 回應，以及純文字輸出檔；iOS 的 `ok`／`data` 與 JSON 輸出格式也保留支援。兩者都核對模型身分、非空內容及已回報的輸出用量／中斷訊號。Android 若沒有回報正常完成狀態，摘要保存為「待核對草稿」，不建立完成摘要標記，也不覆蓋舊摘要。未完成本公開版 Android 真機驗證。格式依據：[官方 Android 橋接實作](https://github.com/OpenMinis/OpenMinis/blob/b4c0661d5631ebab4d1a2e6f3fd4c805d4030a6c/src/android/app/src/main/java/com/openminis/app/sandbox/offload/ModelUseOffloadHandler.kt)。

Groq 請求另加入跨平台的 workflow `User-Agent`，不冒充瀏覽器、不停用 TLS。朋友的修改版也補上這個標頭，但缺少原始失敗回應，因此不能保證這項修正可解決所有連線問題；401／403／429 等錯誤仍須分別檢查帳戶、權限及額度。

## 4. 選取與執行

以下命令假設音檔位於 `/var/minis/shared/錄音輸入/`；若使用掛載目錄，加上 `--source '實際掛載路徑'`。

```sh
# 列出檔案：不需要 Key、不解碼、不上傳、不寫入
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --list

# 預覽最多一份待處理檔案：不上傳、不寫入
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --dry-run

# 正式執行：預設最多一份未完成音檔
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py'

# 指定一份英文錄音，SRT 保留英文，摘要使用繁中
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --file '英文課程.m4a' --language en

# 指定多份；每份加一次 --file
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --file '課程一.m4a' --file '訪談二.mp3'

# 所有尚未以相同轉錄、摘要設定完成的檔案
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --all

# 來源內有子資料夾時才加入 --recursive
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' --all --recursive

# 自訂來源與輸出
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --source '/var/minis/mounts/錄音輸入' \
  --output '/var/minis/shared/錄音逐字稿' --dry-run
```

`--all` 不限份數；`--file` 處理所有明確指定的檔案；其餘模式預設 `--limit 1`。來源檔只讀取，不移動或刪除。檔案依路徑排序，不按錄音時間排序。

也可設定 `GROQ_SOURCE_DIR`、`GROQ_OUTPUT_DIR` 作為日常預設，命令列參數優先。

### 對話啟動範例

先把實際路徑與程式位置告訴 Open Minis，例如：

> 我的 workflow 在 `/var/minis/shared/錄音轉錄工具/groq_workflow.py`，音檔來源是 `/var/minis/mounts/錄音輸入`。請先用 `--list` 列出音檔，再用 `--dry-run` 預覽我選定的檔案。

> 請執行這個 workflow，只處理「英文課程.m4a」，加上 `--language en`，輸出到 `/var/minis/shared/錄音逐字稿`。

> 請先檢查 GPT-6-Sol 已加入「Minis 執行時可調用的模型」，再用新版 workflow 的預設 Minis 摘要處理我選的錄音。若模型不能呼叫，請停止並告訴我，不要改用其他服務。

> 這份錄音已完成轉錄，請用 `--summary-only` 改為 GPT-6-Sol 摘要，不重新上傳音檔；成功後才替換舊摘要。

> 這次我明確選擇 Groq 備案，請加上 `--summary-backend groq`，先預覽指定檔案再執行。

> 如果回傳結果缺少完成狀態，請保留為待核對草稿，不要宣稱已完成摘要。先告訴我完成、待核對及失敗各有幾份。

> 這份錄音的草稿需要重新生成，請指定原音檔，加入 `--summary-only --retry-summary`。不要重傳音訊，也不要覆蓋我已修改的草稿。

自然語言指令由你目前的 Open Minis 對話模型解讀並執行；這些 Python 程式不是 Open Minis 外掛，也不會自動綁定特定聊天標題。

## 5. 語言與摘要模式

- `--language auto`：預設不傳固定語言給轉錄 API。適合先試中文夾英文名稱或語言不確定的錄音，不保證逐句語言切換準確。
- `--language en`：主要英文。
- `--language zh`：主要中文。
- SRT 使用轉錄端點，保留模型辨識出的語言；沒有另外呼叫翻譯端點。
- 預設轉錄模型為 `whisper-large-v3-turbo`；可用 `--model whisper-large-v3` 覆蓋。
- 摘要預設為 `--summary-backend minis`、`--minis-model gpt-6-sol`。從 App 允許清單精確選取，不跟隨聊天模型，也不把作者的供應商識別碼寫入程式。可用 `MINIS_SUMMARY_MODEL`、`MINIS_SUMMARY_PROVIDER` 或對應命令列參數選擇自己的模型項目。
- `--summary-backend groq` 才使用 Groq 備案；該模式預設 `openai/gpt-oss-120b`，`--summary-model` 只影響 Groq，不影響 Minis。
- Minis 單次摘要預設輸出預算 `--minis-max-tokens 6000`、呼叫逾時 `--minis-timeout 600` 秒。這些是上限，不是輸出長度或速度保證；達輸出上限的回應不當成完成摘要。
- `--summary-style original` 完整保留作者原始摘要 prompt；`cautious` 才額外要求不確定的專名、人名、數字標示「待核對」。

```sh
python3 '/var/minis/shared/錄音轉錄工具/groq_workflow.py' \
  --file '課程一.m4a' --summary-style cautious
```

Minis 模式在保守輸入預算內單次讀取完整逐字稿，OAuth／API 認證由 App 管理。腳本以 UTF-8 位元組數作為保守 token 上界，再預留輸出及格式空間，超過模型清單的容量時明確停止；清單未提供容量時採較小的預算。這不是實際 tokenizer 計數，可能比模型真正容量保守，不能保證更長錄音一定可用。

Groq 模式會先分段摘要、逐級整合，保留每個已完成節點；一般 429 等待重試，單次過大或截斷時嘗試縮小片段，仍可能因額度或輸出預算失敗。每次摘要請求至少間隔 60 秒；估計輸入含 prompt 約 3,500 tokens，輸出預算 2,000 tokens。這是保守估計，不是帳戶額度保證。[Groq 官方](https://console.groq.com/docs/rate-limits) 列出的免費方案 `gpt-oss-120b` 為 8,000 TPM，tokens 不等於中文字數；每日總額度足夠也可能遇到每分鐘限速。

兩種模式均依辨識文字摘要，不直接理解完整原音，也可能漏掉細節或放大辨識錯誤。可選審慎模式不能保證正確。

## 6. 輸出與續跑

每份音檔建立 `檔名__識別碼/`，包含 SRT、`_逐字稿.txt`、`metadata.json`、`chunk_*.json`。確認模型正常結束的摘要使用 `_摘要.md` 與 `摘要設定.json`；Groq 分層摘要另有 `摘要進度.json`。這裡的「確認」只指 App／服務回報的結束狀態，不代表內容正確或沒有遺漏。

Android 相容回應若缺少完成狀態，使用 `檔名_摘要_待核對草稿_隨機識別碼.md`，以及按設定區分的 `_摘要草稿設定_*.json`。檔名不綁定任何模型；設定記錄實際模型、逐字稿／prompt／設定及草稿內容雜湊。即使文字看似完整，也不自動升格為完成摘要。請自行核對原逐字稿及重要段落，保留原草稿與驗證紀錄；本版沒有自動「核准草稿」功能。

批次結尾提供完成、新草稿、既有待核對及失敗數量，另有一行 `WORKFLOW_RESULT {JSON}` 供 Agent 讀取。結束碼：`0` 正常完成／略過已完成／預覽，`1` 有失敗，`2` 沒有失敗但仍有待核對草稿，`130` 使用者中斷。混合批次有失敗時優先回傳 `1`。Agent 不應把 `2` 當成崩潰而反覆重跑；詳看 JSON 計數。`--list`／`--check` 不產生這份批次報告。

識別碼根據音檔內容 SHA-256、語言、轉錄模型、解碼工具及分段設定計算。摘要快取另外檢查逐字稿內容 SHA-256、完整 prompt、摘要模型及模式。

- 同設定重跑：沿用完整有效的片段及分段摘要；完成的檔案略過。
- 片段 JSON 損壞：只重做損壞或設定不符的片段。
- 切換摘要模型／模式：使用既有完整逐字稿生成摘要，保留舊摘要版本；不重新上傳音訊。
- 語言、轉錄模型、解碼工具或音檔內容改變：產生另一個輸出目錄。
- `--force`：另開新目錄重做轉錄；不能和 `--summary-only` 併用。
- `--summary-only`：只處理本公開版已完整轉錄的結果，不把未完成的逐字稿當成完整成果。
- 同設定且草稿未被修改：沿用既有待核對草稿，不再呼叫模型，但仍回報待核對，不冒充已完成。
- 草稿手動修改、摘要模型或設定改變：保留舊草稿，另存新稿，不會因舊檔存在而卡住。
- `--retry-summary`：明確重新呼叫 Minis 摘要，保留舊稿。搭配 `--summary-only` 與指定音檔即可不重傳音訊；此選項不適用 Groq 摘要模式。

v0.1.0／v0.2.0 公開版的完整轉錄可沿用；相同設定的 v0.2.0 已完成摘要也可略過，不強制重新生成。原始私人手機版（包括朋友修改版）的識別碼／草稿格式不會自動遷移。先保留舊成果，公開版使用自己的輸出資料夾測試。Minis 單次呼叫中斷時須重送該次摘要，已完成的轉錄仍保留；Groq 可續跑已保存的分層節點。

請一次只執行一個 workflow 實例。iOS 掛起 App、Android 省電限制或程序被終止時，應手動以相同參數重跑；尚未保存的當前片段可能需重新上傳。

## 7. 隱私、限制與授權

- 音訊會以約四分鐘、略有重疊的 16 kHz 單聲道 FLAC 片段送往 Groq。記憶體中組合單段 HTTP 上傳內容，長錄音不會整份載入記憶體。
- 自動副檔名篩選含 MP3、M4A、WAV、FLAC、OGG、MP4、AAC、WEBM；實際可解碼格式取決於裝置上的 FFmpeg／SoX。副檔名支援不代表已逐格式真機測試。
- 重疊合併採時間截斷方式，仍可能在片段邊界出現重複或漏字。
- 沒有說話者分離、即時轉錄、Apple 語音備忘錄自動匯出或持續監看。
- HTTP 429 與暫時性伺服器／連線錯誤最多重試四次，不保證所有額度限制都能自動解決。
- 可使用 Groq 免費方案試跑，但仍受帳戶的請求、音訊時數及文字 token 額度限制；分段處理不會解除這些限制。付費方案則可能產生費用，請先確認 [Groq 帳戶額度](https://console.groq.com/docs/rate-limits) 與計費設定。
- 使用 Minis 摘要也受你所選模型服務、登入方式及帳戶用量政策限制；Codex OAuth 不代表人人有 GPT-6-Sol 權限或無限免費使用。API Key 與 ChatGPT 登入不是同一種計費路線，請確認 App 的供應商設定。本程式不替你登入、取出 OAuth 憑證或切換認證方式。
- 專業術語、人名、數字與口音需要人工核對。請使用你有權交由雲端處理的錄音。
- 金鑰由 Python HTTP 請求使用，不放入外部程序參數。錯誤訊息不輸出 HTTP 回應正文，另有金鑰遮蔽；終端機、Agent、環境變數與裝置備份仍應由你管理存取權限。
- 不將錄音、逐字稿、快取、完整開發對話、環境設定或私人裝置資料加入公開專案。詳見 [公開範圍及資料流](docs/PRIVACY.md)。

作者：Arthur Tao。程式依 [MIT License](LICENSE) 分享；Open Minis、Groq 及所使用模型各有自己的授權與服務條款。本專案為獨立使用者 workflow。

延伸閱讀：[Open Minis 是什麼？讓雲端 AI 模型操作手機原生工具的行動 Agent](https://taoyutsun.blogspot.com/2026/08/open-minis-cloud-ai-mobile-agent.html)。
