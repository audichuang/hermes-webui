# fork-merge playbook

這個 fork 併上游、接收上游 PR、跑測試、上線的完整流程。

> 本檔、`AGENTS.local.md`、`CLAUDE.md` 都被 `.gitignore` 排除,但已 `git add -f` 納入版控
> —— 否則 worktree 一重建就整份消失(2026-08-01 就這樣不見過一次)。
> 本檔是「要用才讀」的那層,不會自動載入。

---

## 0. 紅線

**正本在 `AGENTS.local.md`** —— 那份透過 `CLAUDE.md` 常駐 context,每次都會載入。
這裡刻意不複述,免得兩處漂移。動手前確認你讀到的是那份。

## 1. 地形

| | |
|---|---|
| 實際開發 | `~/research/hermes-webui-projects`,分支 `develop`,埠 8789 |
| 對照用 | `~/research/hermes-webui`,分支 `master`,純追蹤上游、沒服務跑它 |
| 服務 | systemd **user** unit `hermes-webui-projects.service`(unit 內設 `HERMES_WEBUI_PORT=8789`) |

`develop` 不預期 merge 回 `origin`。策略是撿上游沒過的 PR,在 fork 上長成自己要的功能。

**重啟不要用 `ctl.sh`** —— 它在沒有 `HERMES_WEBUI_PORT` 時預設 8787(`ctl.sh:181`),
會起到錯的埠。要用:

```bash
systemctl --user restart hermes-webui-projects.service
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8789/   # 期望 200
```

## 2. 併上游(每日同步)

以上游為基底,**自有修改一律 rebase 疊在最上面**。好處:撿過的 PR 被上游正式合併後,
rebase 會偵測到內容已在上游而自動拋棄我方那個 commit,歷史自動收斂 —— merge 做不到。
前置條件就是紅線 1。

```bash
cd ~/research/hermes-webui-projects
# 前提守衛:toplevel 對、branch 是 develop、git status --porcelain 為空。任一不符就停手。

git fetch origin
git log --oneline develop..origin/master          # 空 → 無更新,結束

git tag -f pre-rebase HEAD                        # 退路
git merge-tree --write-tree --name-only HEAD origin/master   # 預演
#   exit 0 且只吐一行 tree OID → 乾淨
#   有列出檔名 → 有衝突,不要 rebase,回報清單等人處理

git rebase origin/master
HERMES_WEBUI_TEST_PYTHON=/home/audichuang/.local/bin/python3.11 ./scripts/test.sh -q --timeout=300
# 扣掉第 4 節的既有失敗後全綠,才:
git push fork develop --force-with-lease
```

失敗回退:rebase 進行中用 `git rebase --abort`;已完成用 `git reset --hard pre-rebase`。
**絕對不要 push。** 回退前先 `git tag -f rebase-blocked-<日期> HEAD` 把 rebase 結果留著,
免得修的人要重跑一次。

推完才重啟服務,且先看 `git diff pre-rebase HEAD --stat` —— 只有 CHANGELOG.md 動就不用重啟。

### `merge-tree` 預演乾淨 ≠ 安全

**這是這份 playbook 最重要的一句。** `merge-tree` 只看文字衝突。上游和我們各自改同一支
函式的不同位置時,文字合得起來,語意卻壞掉。2026-08-01 就撞到:預演乾淨、rebase 乾淨,
測試卻多出兩個失敗(見第 5 節)。**預演只決定「要不要動手」,測試才決定「能不能推」。**

## 3. 測試

```bash
./scripts/test.sh -q --timeout=300
```

`.venv` 存在且是支援版本(3.11/3.12/3.13)又有 pip 時,腳本直接重用、不碰探測流程,
這樣跑就對了。單跑幾個測試用 `.venv/bin/python -m pytest -q <target>`。

**判讀規則:不要只看 exit code,先確認輸出有 `N passed` 那行。** 正常規模約
13900+ passed / 約 410 秒。只有 4 行輸出 = 沒跑起來,見下。

假綠有第二種形態:**collection error**。任何測試檔在 module 層 import 缺席的套件,
pytest 會 `Interrupted: 1 error during collection` 中止**整套**,結尾長這樣:

```
1 skipped, 9 warnings, 1 error in 21.53s     # 沒有 "N passed",21 秒不是 410 秒
```

2026-08-09 撞到:上游新增的 `test_compression_phantom_barrier.py` module 層裸
`from playwright.sync_api import ...`,本機沒裝 playwright,14330 個測試一個都沒跑。
**辨識法一樣是找 `N passed`;沒有那行就當作沒跑。** 修法見第 5 節同名 delta。

### `.venv` 不見時的陷阱(已用 settings.json 擋掉)

`.venv` 也是 gitignored(第 74 行),**worktree 重建或 `git clean -xdf` 之後會消失**。
沒有防護時腳本會探測 `python3.13 → python3.12 → python3.11 → python3`,撞到本機缺
ensurepip 的 `/usr/bin/python3.12` 就**直接放棄、不會 fallback 到可用的 3.11**。

它失敗得很安靜:只印 4 行、**一個測試都沒跑**。若用 `./scripts/test.sh ... | tail` 取輸出,
`$?` 拿到的是 `tail` 的 0,看起來完全像全綠。2026-08-01 就這樣白跑一次。

**防護已就位**:`.claude/settings.json`(同樣 force-add 進版控)把
`HERMES_WEBUI_TEST_PYTHON` 固定指到 `~/.local/bin/python3.11` —— 那是 uv 管的 Python,
symlink 走不帶 patch 版號的 `cpython-3.11-linux-x86_64-gnu`,升 patch 也不會斷。
探測流程因此永遠不會選到壞掉的 3.12,`.venv` 不存在時會自己正確建起來。
**不要刪掉那個設定。**

設定若失效(例如換機器、路徑變了),手動重建:

```bash
uv venv --seed --python 3.11 .venv     # --seed 才會裝 pip,test.sh 會檢查
```

`--seed` 不能省:`uv venv` 預設不裝 pip,而 test.sh 的重用檢查有一項是「跑得動 pip」,
沒 pip 它會判定要重建,又繞回壞掉的探測流程。

(`sudo apt install python3.12-venv` 也能修探測路徑,但要 sudo,而且裝完自動探測會改用
**系統** Python 建 venv,反而繞開 uv。**不建議。**)

## 4. 既有環境失敗(可扣掉)

這些是本機環境缺件,不是回歸。**只有扣掉這些之後全綠才可以推。**

| 測試 | 症狀 | 成因 |
|---|---|---|
| `test_issue4685_post_compression_context_metering.py::test_post_compression_estimate_uses_compressor_budget_counter_without_metadata_estimators` | `ImportError: cannot import name 'call_llm' from 'agent.auxiliary_client'`(`../hermes-agent/agent/context_compressor.py:28`) | hermes-agent 版本不合 |
| `test_tls_aware_probe.py::test_helper_self_signed_warns_and_succeeds` | `health_probe.sh` returncode 1 | 本機 TLS 探測環境 |
| `test_tls_aware_probe.py::test_helper_insecure_optin_is_silent` | 同上 | 同上 |
| `test_xsession_wakeup_misroute.py::test_turn_identity_binder_restores_previous_value` | `ModuleNotFoundError: No module named 'gateway'` | hermes-agent 未安裝 |

共 4 個測試 / 3 類。

### 另有 6 個 flaky(不是回歸,但也不是「可扣掉」)

- `test_issue3023_safe_session_id_validators.py::test_session_delete_validator_accepts_hyphenated_ids`
- `test_issue4662_sidebar_redaction_read_once.py::test_sessions_search_branches_redact_derived_titles`
- `test_security_redaction.py::test_api_session_redacts_messages`
- `test_security_redaction.py::test_api_session_export_redacts`
- `test_static_asset_resolver.py::test_service_worker_and_favicon_follow_selected_static_root`
- `test_issue5210_http_worker_bound.py::test_worker_slot_releases_after_request_finishes`
  —— 2026-08-08 加入。同一份**產品**程式碼連跑兩輪:第一輪 failed、第二輪沒出現,
  單跑該檔 7 passed。名字就是 timing 敏感型(worker slot 釋放),整套跑時搶資源。

2026-08-01 實測:**同一份程式碼**兩次跑出 9 failed vs 4 failed(總數固定 13969),
5 個一起單獨跑 3.7 秒全過。所以是 flaky,不是回歸。**機制未查明**。

**失敗只落在這 6 個之內 → 先重跑一次完整套件再判斷**,不要立刻假設是自己改壞的
(照「清單以外都要當真」的字面走,會開始找不存在的回歸 —— 當初為此燒了三輪約 21 分鐘)。
要證因果就跑 HEAD 對照組:`git stash push -u` → 跑 → `git stash pop --index`,
先 `git diff --cached > patch` 備份並用 sha256 驗還原。

判斷 flaky 有個比對照組更快的路子:**如果這輪與上輪的產品程式碼差異為零**
(只動了測試 harness 或 .md),而某個測試只在其中一輪失敗,那它就是 flaky ——
不必再跑 stash 對照組。2026-08-08 就是這樣認出 `test_issue5210` 的。

**這 6 個以外的任何失敗仍然都要當真。**

## 5. fork delta 慣例

我們改到**上游檔案**時,加一行 `# fork delta:` 或 `// fork delta:` 註解說明為什麼,
讓下次 rebase 撞到的人知道不能直接丟掉。`grep -rn "fork delta" tests/ static/` 可列出全部。

現有的 delta:

- `AGENTS.md` —— 檔尾兩行,指向 `AGENTS.local.md`。**這是給讀 `AGENTS.md` 的工具用的**
  (codex 等);Claude Code 2.1.220 實測不載入 repo 的 `AGENTS.md`,它走 `CLAUDE.md`。
  只加在檔尾、措辭通用,rebase 撞衝突的機率低。**不要 upstream 這兩行。**
- `tests/test_compression_phantom_barrier.py` —— 上游在 module 層裸 import playwright,
  本機沒裝就 abort 掉**整個** collection(第 3 節那個 21 秒假綠)。補
  `pytest.importorskip("playwright")`,跟 repo 裡另外 11 個瀏覽器測試檔同慣例。
  **上游之後再加 playwright 測試檔,大概要再補一次。**
- `api/routes.py` —— `_handle_memory_read` 的 payload 尾巴多回傳 `memory_enabled` /
  `user_profile_enabled`。上游只在伺服器端 gate、不回傳 flag,而我們的 memory 面板要靠這
  兩個欄位隱藏停用區塊(`static/panels.js` 的 `_memorySectionEnabled`,上游沒有對應物,
  覆蓋在 `tests/test_issue6406_memory_panel_gates.py`)。純 append 兩行,衝突面很小。

教訓一:**我們在 `static/ui.js` 加私有 helper 時,上游測試不可能認得它。**
動 `ui.js` 的共用函式前先想一下有哪些 harness 會抽它。

教訓二(上面那條的鏡像):**上游改掉共用函式的「讀取方式」,我們抽它的 harness 也會認不得。**
裸讀變 `window.*`、變 getter、變參數注入都算,而且症狀是 node 直接 exit 1、不是斷言失敗,
看起來像測試壞掉而不是環境不合。撞到就先看抽出來的函式碰了哪些名字,harness 有沒有餵。

教訓三:**上游正式合併我們撿過的 PR 時,圍繞它長出來的 fork delta 會整批一起死,
而且死法是「重複」不是「缺少」。** 2026-08-09 上游合了 upstream PR 6453,一次帶走三條:
`test_issue6414` 的 window shim(上游自己加了)、`test_issue5637` 的 const 注入(上游
自己寫死了,我方那行變成重複 `const` → node SyntaxError)、`test_issue4856` 的字元窗
放寬(ui.js 回到上游版就不需要了)。

實務上的意思:**撿過的 PR 一旦在上游 log 裡出現同名 commit,先去 grep 那批 fork delta,
別等測試紅了才回頭找。** 上游那個 commit 的標題通常跟我們當初的 commit 標題幾乎一樣
—— 那就是信號。另外注意這種 delta 在 `merge-tree` 預演裡**看不出來**:文字位置不同,
自動合併會成功,壞掉的是語意(重複宣告)。又一個「預演乾淨 ≠ 安全」的實例。

## 6. 接收上游沒合併的 PR

- 找候選看 label(`fully-gated` / `gate-pass`)。**不要看 closed-unmerged** —— 上游有
  gate-rebase 流程,maintainer 自己 rebase 後合併會把原 PR 關成 unmerged,那不等於被拒。
- 「一直沒過」有時是對的:部分 `gate-pass` 掛很久是因為 gate 報告驗出真的安全問題,
  直接撿等於把漏洞一起撿進來。**撿之前先讀報告。**
- 接進來用 `git merge --squash`,**不要 cherry-pick**(PR 分支通常混了數個 merge commit)。
- squash 後自己寫 commit message,原 PR 的 `#編號` 要清掉(紅線 1)。可在 message 寫
  `(upstream PR 5771)` 這種不帶 `#` 的形式。
- **編號抄 PR 號,不要抄 issue 號。** projects db 那批既有 commit 標的 `5763` 是 issue
  (「migrate WebUI Projects to Hermes Agent upstream projects.* JSON-RPC surface」),
  實際 PR 是 **5771**。上游習慣把 PR 標題寫成 `fix(#<issue>): ...`,squash 時很容易把
  issue 號當成 PR 號帶進來。已進歷史的改不動,新的別再錯。

## 7. 試過但沒過的:離線緩衝位元組上限

2026-08-01~02 給 `StreamChannel._offline_buffer`(`api/config.py:8628`)加位元組上限
(upstream issue 6351)。做到 850 行,七輪 codex adversarial review 仍是 NOT SHIPPABLE,
**已從 `develop` 抽掉**,工作留在 tag **`byte-cap-wip`**。`develop` 維持上游行為
(只有 `_OFFLINE_BUFFER_MAXLEN` = 8192 這個**數量**上限)。

**教訓不是「哪個數字算錯」,是「對這個設計的極限的刻畫本身不可靠」。** 三次把殘留寫成
「可接受、有界」,三次被下一輪證明界限是錯的:取樣外推低報 1,114 倍;超大 frame 從
「約 2.05x 上限」變 2.865x,外加從未量化的 6-8x enqueue 尖峰;呼叫端變更的窗口從
「一個 frame 寬」變成活到佇列被消費、可無界成長。估算一個**會變、且由呼叫端擁有**的
物件,本質上就是在追移動目標。

**重做別再從「加上限 + 估算大小」出發。** 要嘛在 enqueue 當下產生**不可變表示**並保留
(注意 JSON round-trip 會把 aliased value 展開,對別名密集的 payload 反而更耗記憶體),
要嘛讓超大 frame 走明確的 journal 復原路徑。動手前先讀 `subscribe_with_snapshot()`
(`api/config.py:8700`)那段註解 —— 重播契約寫在那裡,而且**丟掉最新的 frame 會讓分頁
停在 heartbeat 等下去**(事件在 journal 裡,但復原程式不會去拿)。
