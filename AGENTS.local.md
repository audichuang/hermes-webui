# AGENTS.local.md — 這個 fork 的紅線

動手前必讀。完整流程(併上游、測試、上線、接收上游 PR)在
**`.claude/fork-merge-playbook.md`**。非瑣碎的產品改動另讀 `docs/GUIDELINES.md`。

> `CLAUDE.md`、本檔、`.claude/` 都在 `.gitignore` 裡,但**已用 `git add -f` 強制納入版控** ——
> 不這樣它們會在 worktree 重建後消失。日後改 `.gitignore` 不要把它們重新排除掉。
> commit 時 `git add .claude/...` 會吐警告並回 exit 1(檔案其實有進 index),加 `-f` 就安靜。
>
> **`CLAUDE.md`(只有一行 `@AGENTS.local.md`)是唯一會自動載入的入口。**
> Claude Code 2.1.220 實測**不載入 repo 的 `AGENTS.md`** —— 別把 fork 紅線放那裡。

## 紅線

1. **commit message 絕不出現 `#<數字>`,含 body。** 會在上游 PR 底下灌 cross-reference,
   原作者看得到;而且我們用 rebase,每次 rebase 都會再污染一次。
   要引用上游 PR 就寫 `(upstream PR 5771)` 這種不帶 `#` 的形式。
   **編號要是 PR 號,不是 issue 號** —— projects db 那批既有 commit 寫的 5763 其實是
   issue 編號,PR 是 5771,已經進歷史改不動了,別照抄。
2. **force push 先問。** 唯一例外:每日同步排程,且「`merge-tree` 預演乾淨」與
   「測試扣掉既有失敗後全綠」兩關都過。人工介入修過的分支要重新問。
3. **remote 語意是反的**:
   - `origin` = **上游** `nesquena/hermes-webui`
   - `fork` = **自己的** `audichuang/hermes-webui`
   - **沒有 `upstream` 這個 remote。** 推錯地方等於把私有分支推上上游。

## 兩件最容易踩的

- **測試**:直接 `./scripts/test.sh -q --timeout=300`。**判讀一律看 `N passed` 那行,不要只看
  exit code**(正常約 13900+ passed / 約 410 秒;只印 4 行 = 一個測試都沒跑)。
  `.claude/settings.json` 的 `HERMES_WEBUI_TEST_PYTHON` **不要刪** —— 沒它會選到本機缺
  ensurepip 的 python3.12 而靜默跑零測試。原委、既有失敗清單與 flaky 清單見 playbook 第 3、4 節。
- **重啟服務用 `systemctl --user restart hermes-webui-projects.service`,不要用 `ctl.sh`**
  (它預設 8787,實際跑 8789)。
