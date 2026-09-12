#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
訴願逾期（訴願法 §14 / §77 第 2 款）規則引擎 —— 程序審查模型 v1 的第一個規則。

設計原則：可解釋、每一步都能回到法條。輸入是「已結構化的程序事實」，
不做自然語言理解（那是抽取器 extract_overdue_facts.py 的事）。

法律依據：
  訴願法 §14 I   自行政處分達到或公告期滿之次日起 30 日內
  訴願法 §14 III 以原處分機關或受理訴願機關收受訴願書之日期為準
  訴願法 §16     訴願人不在受理訴願機關所在地住居者，應扣除在途期間
  訴願扣除在途期間辦法 附表（本檔 IN_TRANSIT_TABLE，受理機關＝新北市政府）
  行政程序法 §72 本人／會晤處所送達 → 送達日即生效
            §73 補充送達（同居人／受雇人／接收郵件人員）→ 交付日生效
            §74 寄存送達 → 依實務及法務部 93.4.13 法律字第 0930014628 號函釋，
                          以「寄存之日」視為收受送達日
                          （註：釋字第 797 號後仍採此見解；如未來改採「寄存日
                           加 10 日」，改 STORAGE_GRACE_DAYS 即可）
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field, asdict
from typing import Optional

APPEAL_PERIOD_DAYS = 30
STORAGE_GRACE_DAYS = 0  # 寄存送達的猶豫期；實務現採 0

# 受理訴願機關＝新北市政府 時，訴願人住居所在他縣市之在途期間（日）
# 來源：訴願扣除在途期間辦法 附表；已與 110–114 新北訴願決定書語料交叉核對
IN_TRANSIT_TABLE = {
    "新北市": 0, "臺北市": 2, "基隆市": 2, "桃園市": 3, "宜蘭縣": 5,
    "新竹市": 4, "新竹縣": 4, "苗栗縣": 4, "臺中市": 5, "彰化縣": 6,
    "南投縣": 7, "雲林縣": 7, "嘉義市": 7, "嘉義縣": 7, "臺南市": 4,
    "高雄市": 6, "屏東縣": 8, "花蓮縣": 5, "臺東縣": 7,
    "澎湖縣": 11, "金門縣": 12, "連江縣": 15,
}

DELIVERY_METHODS = {"本人", "補充", "寄存", "公示", "面交", "未知"}

# 期間末日順延用的國定假日（行政程序法 §48 II）。
# 春節以「政府行政機關辦公日曆表」放假起訖概估；固定節日逐年展開。
# 註：非權威來源，正式版應改讀行政院人事行政總處 open data。
_LUNAR_NEW_YEAR = {
    2021: ("2021-02-10", "2021-02-16"), 2022: ("2022-01-29", "2022-02-06"),
    2023: ("2023-01-20", "2023-01-29"), 2024: ("2024-02-08", "2024-02-14"),
    2025: ("2025-01-25", "2025-02-02"), 2026: ("2026-02-14", "2026-02-22"),
}
# ⚠️ 不含 05-01 勞動節：那是勞動基準法給**勞工**的假，**行政機關照常上班**，
# 故非行政程序法 §48 II 所稱休息日。誤列會讓系統在自己其實算對時，
# 反過來叫承辦人採信錯的一方（2026-09-12 實測抓到）。
_FIXED_MMDD = ["01-01", "02-28", "04-04", "04-05", "10-10"]


def _holidays():
    hs = set()
    for y, (s, e) in _LUNAR_NEW_YEAR.items():
        d0, d1 = dt.date.fromisoformat(s), dt.date.fromisoformat(e)
        d = d0
        while d <= d1:
            hs.add(d)
            d += dt.timedelta(days=1)
    for y in range(2020, 2028):
        for mmdd in _FIXED_MMDD:
            hs.add(dt.date.fromisoformat(f"{y}-{mmdd}"))
    return hs


HOLIDAYS = _holidays()


def _is_rest_day(d: dt.date) -> bool:
    return d.weekday() >= 5 or d in HOLIDAYS


def _d(x) -> Optional[dt.date]:
    if x in (None, ""):
        return None
    if isinstance(x, dt.date):
        return x
    return dt.date.fromisoformat(str(x)[:10])


def _norm_city(name: str) -> str:
    if not name:
        return ""
    return name.replace("台", "臺").strip()


@dataclass
class TimelinessInput:
    delivery_date: Optional[str] = None          # 裁處書送達日（原始送達動作日）
    delivery_method: str = "未知"                # 本人 / 補充 / 寄存 / 公示
    appeal_filed_date: Optional[str] = None      # 收受訴願書之日（機關收文日）
    appellant_city: str = "新北市"               # 訴願人住居所縣市
    disposition_is_public_notice: bool = False   # 原處分以公告方式者
    public_notice_expire_date: Optional[str] = None
    remedy_notice_correct: bool = True           # 原處分是否已正確教示救濟期間
    force_majeure: bool = False                  # 主張天災等不可歸責事由（§15）


@dataclass
class TimelinessResult:
    verdict: str                       # 逾期 / 未逾期 / 資料不足
    is_overdue: Optional[bool]
    effective_service_date: Optional[str] = None   # 送達生效日
    period_start_date: Optional[str] = None        # 起算日（生效日次日）
    in_transit_days: int = 0
    deadline_date: Optional[str] = None            # 法定期間屆滿日
    days_overdue: Optional[int] = None
    legal_refs: list = field(default_factory=list)
    steps: list = field(default_factory=list)      # 逐步說明，供承辦查證
    missing: list = field(default_factory=list)    # 缺哪些資料才能判斷

    def to_dict(self):
        return asdict(self)


def check_timeliness(inp: TimelinessInput) -> TimelinessResult:
    steps, refs, missing = [], [], []
    city = _norm_city(inp.appellant_city) or "新北市"

    # ---- 1. 送達生效日 ----
    eff = None
    if inp.disposition_is_public_notice:
        eff = _d(inp.public_notice_expire_date)
        if eff:
            steps.append(f"原處分以公告方式，依訴願法 §14 I 自公告期滿日（{eff}）計算。")
            refs.append("訴願法 §14 I")
        else:
            missing.append("public_notice_expire_date（公告期滿日）")
    else:
        sd = _d(inp.delivery_date)
        if not sd:
            missing.append("delivery_date（裁處書送達日）")
        else:
            m = inp.delivery_method or "未知"
            if m == "寄存":
                eff = sd + dt.timedelta(days=STORAGE_GRACE_DAYS)
                refs.append("行政程序法 §74；法務部 93.4.13 法律字第 0930014628 號函釋")
                steps.append(
                    f"寄存送達：以寄存日 {sd} 為收受送達日"
                    + (f"（加猶豫期 {STORAGE_GRACE_DAYS} 日＝{eff}）" if STORAGE_GRACE_DAYS else "")
                    + "。")
            elif m in ("補充", "同居人", "受雇人"):
                eff = sd
                refs.append("行政程序法 §73")
                steps.append(f"補充送達：交付有辨別事理能力之同居人／受雇人／接收郵件人員之日 {sd} 生效。")
            elif m in ("本人", "面交"):
                eff = sd
                refs.append("行政程序法 §72")
                steps.append(f"本人收受：送達日 {sd} 生效。")
            elif m == "公示":
                eff = sd
                refs.append("行政程序法 §78、§81")
                steps.append(f"公示送達：以生效日 {sd} 計（另須確認公示送達生效日之計算）。")
            else:
                eff = sd
                steps.append(f"送達方式未標明，暫以送達日 {sd} 為生效日（需承辦確認送達方式）。")
                missing.append("delivery_method（送達方式：本人/補充/寄存/公示）")

    if eff is None:
        return TimelinessResult(
            verdict="資料不足", is_overdue=None,
            legal_refs=refs, steps=steps, missing=missing)

    # ---- 2. 起算日：生效日之次日（§14 I「次日起算」）----
    start = eff + dt.timedelta(days=1)
    refs.append("訴願法 §14 I（次日起算）")
    steps.append(f"起算日＝送達生效日之次日＝{start}。")

    # ---- 3. 在途期間（§16 + 辦法）----
    transit = IN_TRANSIT_TABLE.get(city)
    if transit is None:
        transit = 0
        steps.append(f"訴願人住居所「{city}」不在在途期間表中，暫以 0 日計（需查辦法附表）。")
        missing.append(f"in_transit_days（{city} 之在途期間）")
    elif transit > 0:
        refs.append("訴願法 §16；訴願扣除在途期間辦法")
        steps.append(f"訴願人住居所在{city}，非受理機關（新北市）所在地，扣除在途期間 {transit} 日。")
    else:
        steps.append("訴願人住居所在新北市，無在途期間可扣除。")

    # ---- 4. 屆滿日 ----
    # 30 日不變期間：起算日 + 30 - 1；再加在途期間
    deadline = start + dt.timedelta(days=APPEAL_PERIOD_DAYS - 1 + transit)
    # 期間末日為星期例假日或國定假日者順延（行政程序法 §48 II）
    raw_deadline = deadline
    while _is_rest_day(deadline):
        deadline += dt.timedelta(days=1)
    if deadline != raw_deadline:
        steps.append(f"末日 {raw_deadline} 為例假日／國定假日，依行政程序法 §48 II 順延至 {deadline}。")
    steps.append(
        f"法定期間屆滿日＝起算日 + {APPEAL_PERIOD_DAYS} 日"
        + (f" + 在途 {transit} 日" if transit else "") + f"＝{deadline}。")

    if not inp.remedy_notice_correct:
        deadline = start + dt.timedelta(days=365 + transit)
        refs.append("訴願法 §14 I、行政程序法 §98 III（未教示或教示錯誤，1 年內提起視為未逾期）")
        steps.append("原處分未正確教示救濟期間 → 屆滿日延長為處分送達後 1 年。")

    # ---- 5. 比對訴願提起日 ----
    filed = _d(inp.appeal_filed_date)
    if not filed:
        missing.append("appeal_filed_date（機關收受訴願書之日）")
        return TimelinessResult(
            verdict="資料不足", is_overdue=None,
            effective_service_date=eff.isoformat(), period_start_date=start.isoformat(),
            in_transit_days=transit, deadline_date=deadline.isoformat(),
            legal_refs=_dedup(refs), steps=steps, missing=missing)

    refs.append("訴願法 §14 III（以收受訴願書之日為準）")
    overdue = filed > deadline
    days_over = (filed - deadline).days if overdue else 0

    if inp.force_majeure and overdue:
        steps.append("訴願人主張不可歸責事由（§15 回復原狀）→ 需個案認定，本引擎不下結論。")
        return TimelinessResult(
            verdict="需人工認定（主張回復原狀）", is_overdue=None,
            effective_service_date=eff.isoformat(), period_start_date=start.isoformat(),
            in_transit_days=transit, deadline_date=deadline.isoformat(),
            days_overdue=days_over, legal_refs=_dedup(refs + ["訴願法 §15"]),
            steps=steps, missing=missing)

    steps.append(
        f"機關收受訴願書之日 {filed} "
        + (f"晚於屆滿日 {deadline}，逾期 {days_over} 日。" if overdue
           else f"未晚於屆滿日 {deadline}，未逾期。"))

    return TimelinessResult(
        verdict="逾期" if overdue else "未逾期",
        is_overdue=overdue,
        effective_service_date=eff.isoformat(),
        period_start_date=start.isoformat(),
        in_transit_days=transit,
        deadline_date=deadline.isoformat(),
        days_overdue=days_over,
        legal_refs=_dedup(refs),
        steps=steps,
        missing=missing,
    )


def _dedup(xs):
    seen, out = set(), []
    for x in xs:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out


if __name__ == "__main__":
    # 範例：新北訴願決定書 1101140004
    demo = TimelinessInput(
        delivery_date="2020-10-28", delivery_method="寄存",
        appellant_city="臺北市", appeal_filed_date="2020-12-17")
    res = check_timeliness(demo)
    import json
    print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
