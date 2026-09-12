"""B4.3：手動挑進卷宗的法規，N4 查不到就要在右欄說查不到。

契約 v2 §3.5.2（Ci 拍板 (b)）：使用者手動加進卷宗的法規會被 join 成單一字串，
當成 **查詢詞** 餵回 N4（`overrides.n4_query`，由 `backend/llm/chat.py` 組）。
餵的是查詢詞不是答案——**N4 查得到才會進 `laws[]`，查不到就是查不到**。

這帶來一個必須顯示的狀態：使用者挑了一條法規但 N4 查不到，它不會進草稿。
**這不是 bug，是誠實**；但畫面要說得出來，否則使用者一樣覺得系統吃掉了他的東西。

## 這件事有兩半，兩半都要在，但比對規則只有一份

- **Epic C 那半**（`backend/llm/chat.py:unmatched_picks`）：chat 回合**當下**
  在 `tool_result.picked_laws` 告訴使用者每一條走到哪。一次性。
- **這一半**：右欄那一項**持續**標著狀態。關掉對話再打開狀態還要在，
  那就得寫進 `manifest.json`。

> **2026-09-13 Ci 改判：兩態改三態。** 契約 §3.5.2 末段原文是「右欄該項標
> 『檢索未命中，未進入草稿』」，**那句是錯的**：純法規名對法條查表必然零命中
> （查表只認「法名第N條」），但那個詞確實進了相似案檢索。說它「未進入草稿」
> 對，說它「未命中」把「有被用到」講成「沒被用到」。契約待改，語意以本檔與
> `chat.py` 的 `PICK_*` 為準。

**比對規則不在本檔，在 `classify_picks`。** 兩份比對就是兩套判準，遲早出現
「chat 回合說這條沒命中、右欄卻標著命中」，而使用者無從判斷哪個是真的。
這與契約 §4.4 要求 `sections[]` 只能有一份實作是同一個道理。

**紅線（proposal B4.3）：不得為了讓它出現而直接塞進 `laws[]`。**
本檔只寫「標記」，一行都不碰 `payload["laws"]`——那份清單是 N4 檢索的結果，
往裡面塞一筆沒查到的東西，就是讓「引用一定查得到」變成必然，
正是 2026-09-05 拍板改成 N4 獨立檢索要防的那件事。

**本檔刻意不 import `store`**：`store.record_run` 要呼叫這裡，反向 import 會成環。
這一層是純函式，沒有 I/O。
"""
from __future__ import annotations

from typing import Any

from backend.llm.chat import (
    PICK_MATCHED,
    PICK_NOTES,
    PICK_QUERY_ONLY,
    PICK_UNUSED,
    classify_picks,
)

#: `laws[].retrieval_status` 的值域（2026-09-13 Ci 改判成三態，見 chat.py 的 PICK_*）。
#:
#: 舊版只有 hit／miss，而那個 miss 是**一句假話**：純法規名對法條查表必然零命中
#: （查表只認「法名第N條」），但那個詞確實進了相似案檢索。說它「未命中、未進入草稿」
#: 把「有被用到」講成「沒被用到」——假在誠實的方向，所以一直沒人抓到。
RETRIEVAL_HIT = PICK_MATCHED
RETRIEVAL_QUERY_ONLY = PICK_QUERY_ONLY
RETRIEVAL_MISS = PICK_UNUSED
#: 還沒跑過草稿的法規是第四種狀態，**不是 miss**：沒查過與查不到是兩件事，
#: 合成一個值會讓剛加入的法規當場被標成「沒用到」。
RETRIEVAL_UNKNOWN = "unknown"

#: 三種文案**直接沿用 `backend/llm/chat.py` 的 `PICK_NOTES`**，不在這裡另寫一份——
#: 右欄的標記與 chat 回合裡講的話必須逐字相同，兩處各寫一句，
#: 哪天改了一邊就會出現「畫面說 A、對話說 B」。
NOTE_HIT = PICK_NOTES[PICK_MATCHED]
NOTE_QUERY_ONLY = PICK_NOTES[PICK_QUERY_ONLY]
NOTE_MISS = PICK_NOTES[PICK_UNUSED]


def classify_law_retrieval(manifest_laws: list[dict[str, Any]],
                           payload_laws: list[dict[str, Any]],
                           query_terms: Any = None) -> list[dict[str, Any]]:
    """回一份標好 `retrieval_status`／`retrieval_note` 的新清單。**不改傳入的物件。**

    比對規則見檔頭：委託給 `backend/llm/chat.py:classify_picks`。那支多做了三件事
    （抹掉全形空白——KB 檔名是人整理的真的會有；把空名字當成「manifest 那筆壞了」
    而不是「檢索沒命中」——混著報會讓承辦人去查一個不存在的檢索問題；
    分得出「只進相似案檢索」與「完全沒用到」），這裡不重寫一份較弱的版本。

    `query_terms` 來自 run payload 的 `retrieval_meta.case_query_extra_terms`
    ——**這一輪真的送進通道 B 的詞**，不是推論。拿不到就退成兩態
    （沒有依據就不宣稱「有用於相似案檢索」，那句話會變成沒有根據的安慰）。
    """
    by_id = {str(p.get("id")): p
             for p in classify_picks(manifest_laws, payload_laws, query_terms)}
    out: list[dict[str, Any]] = []
    for law in manifest_laws:
        entry = dict(law)
        pick = by_id.get(str(entry.get("id")))
        if pick is None:
            # `classify_picks` 只略過「名字是空的」那種壞資料。那不是檢索的事，
            # 所以標 unknown 而不是 miss——標 miss 會叫人去查一個不存在的檢索問題。
            entry["retrieval_status"] = RETRIEVAL_UNKNOWN
            entry["retrieval_note"] = ""
        else:
            entry["retrieval_status"] = pick["state"]
            entry["retrieval_note"] = pick["note"]
        out.append(entry)
    return out


__all__ = ["NOTE_HIT", "NOTE_MISS", "NOTE_QUERY_ONLY", "RETRIEVAL_HIT",
           "RETRIEVAL_MISS", "RETRIEVAL_QUERY_ONLY", "RETRIEVAL_UNKNOWN",
           "classify_law_retrieval"]
