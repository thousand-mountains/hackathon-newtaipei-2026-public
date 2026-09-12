# Project Constitution: hackathon-newtaipei-2026

> **本檔不是本專案的憲章，只是一個指路標。**
>
> 真正的憲章在 repo 根目錄：[`../CONSTITUTION.md`](../CONSTITUTION.md) —— 九原則
> （分層誠實／引用必可驗／不編造測資／規則引擎零 LLM／plan 先行／資料隔離／secret／30h 紀律）。
>
> **請讀那一份，不要讀這一份。**

## 為什麼會有這個檔

`prospec init`（2026-09-12 由 `-76` session 執行）會無條件在 `base_dir` 底下建立一份
`CONSTITUTION.md` 範本。它的預設內容是全空的佔位符（`### 1. [Principle Name]`、
`- [ ] [Constraint 1: …]`），而 prospec 的 skill 流程會指示 AI agent
「Read `{{constitution_path}}`」，也就是**讀這一份**。

如果放著不管，任何照 prospec 流程走的 agent 都會讀到一份空白範本，
然後以為這個專案沒有任何原則與紅線——而本專案的紅線（尤其分層誠實、不編造）
是整個產品的差異化基礎。**那是不能接受的失敗模式，所以這份範本被改成指路檔。**

repo 根的 `CONSTITUTION.md` 沒有被 `prospec init` 覆蓋（已實測確認，md5 前後一致）。
被覆蓋的是 `AGENTS.md`，當時已先備份並還原。

## 對 prospec 流程的效果

`.prospec.yaml` 的 `paths.base_dir: prospec`，所以 `{{constitution_path}}`
解析到本檔。讀到本檔的 agent 會被導去 `../CONSTITUTION.md`，
Constitution Check 一律以那一份的九原則為準。
