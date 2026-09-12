"""把雲端識別資訊從**要送出去的字串**裡拿掉。零依賴（stdlib only）。

## 為什麼獨立一個模組

實作本來想放 `backend/api/dossier.py`（用它的地方），但那個檔頂層 import fastapi，
而 `backend/tests/` 不在零依賴掃描的豁免名單裡——放那裡就**測不到**。
這條是紅線層級的東西（CLAUDE.md：帳號 ID、bucket 名一律只在 `.env`／`~/.aws`），
測不到不行。

## 它防的是什麼（2026-09-13 雲上實測）

拿一個**不存在**的法規 id 打 `POST /api/cases/{id}/laws`，拿到 502，body 是：

    ClientError: An error occurred (AccessDenied) when calling the GetObject
    operation: User: arn:aws:sts::<12 碼帳號 id>:assumed-role/<role>/<session>
    is not authorized to perform: s3:ListBucket on resource: "arn:aws:s3:::<bucket>"

**帳號 ID 與 bucket 名整個印在承辦人與評審的螢幕上。** demo 當天有人手滑點到一個
查不到的法規就會看到。

## 它不是唯一一道

該分類的要在上游分類掉（`corpus._is_masked_missing_key` 把偽裝成 403 的
「找不到」翻成 404）。這裡守的是**還是有東西漏過來**的情況——下一個人新增一種
例外時不必記得這條紅線，也不會外洩。兩道都要有：只有分類的話，沒想到的錯誤型別
會直接外洩；只有遮蔽的話，使用者會看到一句遮成馬賽克的權限錯誤而不是「找不到」。
"""
from __future__ import annotations

import re
from typing import Callable

#: `arn:` 開頭吃到空白或引號為止——帳號 ID 在 arn 裡面，一起被蓋掉。
#: 中文全形括號也當邊界：訊息常被包在「（…）」裡。
_ARN = re.compile(r"arn:[a-z0-9-]*:[^\s\"'）)]+")
_ROLE = re.compile(r"\bassumed-role/[^\s\"'）)]+")
#: 落單的 12 碼數字（不在 arn 裡的那些）。合成案號是 10 碼，不會誤中。
_ACCOUNT = re.compile(r"\b\d{12}\b")


def redact(text: str, bucket_of: Callable[[], str | None] | None = None) -> str:
    """回傳可以安全送出去的版本。

    `bucket_of` 注入是刻意的：bucket 名**不是常數**，而且它的值本身就是不能寫進
    repo 的東西（CONSTITUTION §7），所以不能在這裡硬編一份 pattern，
    也不該讓這個模組去 import 設定層。呼叫端傳 `settings.kb_bucket`。
    """
    out = _ARN.sub("<arn 已遮蔽>", text)
    out = _ROLE.sub("<role 已遮蔽>", out)
    out = _ACCOUNT.sub("<帳號 已遮蔽>", out)
    if bucket_of is not None:
        bucket = bucket_of()
        if bucket:
            out = out.replace(bucket, "<bucket 已遮蔽>")
    return out


__all__ = ["redact"]
