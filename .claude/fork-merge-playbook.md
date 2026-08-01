# fork-merge playbook

這個 fork 併上游、接收上游 PR、跑測試、上線的完整流程。

> 本檔與 `AGENTS.local.md` 都被 `.gitignore` 排除(第 20、22 行),是純 local 檔,
> **不會跟著 clone/worktree 走**。2026-08-01 查證:兩者在全機與 git 全歷史中皆不存在,
> 這份是依當天實際跑過的流程重建的。紅線一併收在本檔,不要再散到別處。

---

## 0. 紅線

1. **commit message 絕不出現 `#<數字>`,含 body。** 會在上游 PR 底下灌 cross-reference,
   原作者看得到。而且我們用 rebase,每次 rebase 都會再污染一次。
2. **不加 `Co-Authored-By` 或任何 attribution 行**(`~/.claude/settings.json` 已強制)。
3. **force push 先問。** 唯一例外:每日同步排程,且「預演乾淨」與「測試扣掉既有失敗後全綠」
   兩關都過。人工介入修過的分支要重新問。
4. **remote 語意是反的**:`origin` = 上游 `nesquena/hermes-webui`,`fork` = 自己的
   `audichuang/hermes-webui`。**沒有 `upstream` 這個 remote。** 推錯地方等於把私有分支
   推上上游。

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
HERMES_WEBUI_TEST_PYTHON=/home/audichuang/.local/bin/python3.11 ./scripts/test.sh -q --timeout=300
```

**一定要帶那個環境變數。** 腳本探測順序是 `python3.13 → python3.12 → python3.11 → python3`,
這台機器的 `/usr/bin/python3.12` 缺 ensurepip(`python3.12-venv` 沒裝),建 `.venv` 失敗後
腳本**直接放棄、不會 fallback 到可用的 3.11**。

危險在於它失敗得很安靜:只印 4 行、**一個測試都沒跑**。若用 `./scripts/test.sh ... | tail`
取輸出,`$?` 拿到的是 `tail` 的 0,看起來完全像全綠。

**判讀規則:不要只看 exit code,先確認輸出有 `N passed` 那行。** 正常規模約
13900+ passed / 約 410 秒。只有 4 行輸出 = 沒跑起來。

`.venv` 在 repo root、已被 gitignore(第 74 行),建好可重用;單跑幾個測試用
`.venv/bin/python -m pytest -q <target>`。

## 4. 既有環境失敗(可扣掉)

這些是本機環境缺件,不是回歸。**只有扣掉這些之後全綠才可以推。**

| 測試 | 症狀 | 成因 |
|---|---|---|
| `test_issue4685_post_compression_context_metering.py::test_post_compression_estimate_uses_compressor_budget_counter_without_metadata_estimators` | `ImportError: cannot import name 'call_llm' from 'agent.auxiliary_client'`(`../hermes-agent/agent/context_compressor.py:28`) | hermes-agent 版本不合 |
| `test_tls_aware_probe.py::test_helper_self_signed_warns_and_succeeds` | `health_probe.sh` returncode 1 | 本機 TLS 探測環境 |
| `test_tls_aware_probe.py::test_helper_insecure_optin_is_silent` | 同上 | 同上 |
| `test_xsession_wakeup_misroute.py::test_turn_identity_binder_restores_previous_value` | `ModuleNotFoundError: No module named 'gateway'` | hermes-agent 未安裝 |

共 4 個測試 / 3 類。**清單以外的任何失敗都要當真。**

## 5. fork delta 慣例

我們改到**上游檔案**時,加一行 `# fork delta:` 或 `// fork delta:` 註解說明為什麼,
讓下次 rebase 撞到的人知道不能直接丟掉。`grep -rn "fork delta" tests/ static/` 可列出全部。

現有的 delta(2026-08-01):

- `tests/test_issue4856_android_scroll_regression.py` —— 測試用寫死的字元窗擷取
  `_recordNonMessageScrollIntent` 再做 substring 斷言。上游和我們各自加長這支函式後
  疊起來超出窗,窗放寬到 2000。**上游若再加長,這裡會再爆,繼續放寬即可。**
- `tests/test_issue5637_stale_anchor_guard.py` —— node harness 按名單抽函式。我們的
  `_freshProgrammaticScrollActive` 要加進名單,它讀的 `PROGRAMMATIC_SCROLL_VALID_MS`
  是 module-level const、`_extract_js_function` 抽不到,直接從 `ui.js` 取實際那行注入
  (不要在測試裡複製一份數值,會漂移)。

教訓:**我們在 `static/ui.js` 加私有 helper 時,上游測試不可能認得它。**
動 `ui.js` 的共用函式前先想一下有哪些 harness 會抽它。

## 6. 接收上游沒合併的 PR

- 找候選看 label(`fully-gated` / `gate-pass`)。**不要看 closed-unmerged** —— 上游有
  gate-rebase 流程,maintainer 自己 rebase 後合併會把原 PR 關成 unmerged,那不等於被拒。
- 「一直沒過」有時是對的:部分 `gate-pass` 掛很久是因為 gate 報告驗出真的安全問題,
  直接撿等於把漏洞一起撿進來。**撿之前先讀報告。**
- 接進來用 `git merge --squash`,**不要 cherry-pick**(PR 分支通常混了數個 merge commit)。
- squash 後自己寫 commit message,原 PR 的 `#編號` 要清掉(紅線 1)。可在 message 寫
  `(upstream PR 5763)` 這種不帶 `#` 的形式 —— 現有 commit 就是這樣寫的。
