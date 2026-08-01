# AGENTS.local.md — 這個 fork 的紅線

動手前必讀。完整流程(併上游、測試、上線、接收上游 PR)在
**`.claude/fork-merge-playbook.md`**。

> 這兩份都被 `.gitignore` 排除(第 20、22 行),但**已用 `git add -f` 強制納入版控**。
> 2026-08-01 之前它們只是 local 檔,worktree 一重建就消失 —— 那正是它們曾經不見的原因。
> 若日後在 `.gitignore` 加了新規則,不要把這兩個檔重新排除掉。
>
> 改完要 commit 時,`git add .claude/fork-merge-playbook.md` 會對著被 ignore 的 `.claude/`
> 吐警告並回 exit 1(檔案其實有進 index)。加 `-f` 就安靜了。

## 紅線

1. **commit message 絕不出現 `#<數字>`,含 body。** 會在上游 PR 底下灌 cross-reference,
   原作者看得到;而且我們用 rebase,每次 rebase 都會再污染一次。
   要引用上游 PR 就寫 `(upstream PR 5763)` 這種不帶 `#` 的形式。
2. **不加 `Co-Authored-By` 或任何 attribution 行。**
3. **force push 先問。** 唯一例外:每日同步排程,且「`merge-tree` 預演乾淨」與
   「測試扣掉既有失敗後全綠」兩關都過。人工介入修過的分支要重新問。
4. **remote 語意是反的**:
   - `origin` = **上游** `nesquena/hermes-webui`
   - `fork` = **自己的** `audichuang/hermes-webui`
   - **沒有 `upstream` 這個 remote。** 推錯地方等於把私有分支推上上游。

## 三件最容易踩的

- **測試**:`.venv` 存在時 `./scripts/test.sh` 直接重用,一切正常。**`.venv` 不見時**
  (worktree 重建、`git clean -xdf`)腳本會探測 `python3.13 → 3.12 → 3.11`,撞到本機缺
  ensurepip 的 `/usr/bin/python3.12` 就**直接放棄、不 fallback**,只印 4 行、**零測試**。
  遇到就先用 uv 重建,再照常跑:

  ```bash
  uv venv --seed --python 3.11 .venv     # --seed 才有 pip,test.sh 需要
  ```

  **判讀一律看 `N passed` 那行,不要只看 exit code**(正常約 13900+ passed / 約 410 秒)。
- **重啟服務用 `systemctl --user restart hermes-webui-projects.service`,不要用 `ctl.sh`**
  (它預設 8787,實際跑 8789)。
- **`merge-tree` 預演乾淨 ≠ 安全。** 它只看文字衝突,看不到語意衝突。預演決定「動不動手」,
  測試才決定「能不能推」。
