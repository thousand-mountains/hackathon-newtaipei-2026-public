"""CLI：對一個合成案例跑完整六節點流程，輸出三層 JSON。

    python3 -m backend.cli --case synthetic-ordinary-01
    python3 -m backend.cli --case synthetic-blocked-01 --out backend/output/
    python3 -m backend.cli --list

退出碼：
- 0：流程跑完（**包含守門攔下的情形**——攔下來是正確行為，不是失敗）
- 1：流程本身出錯（找不到案例、不變式違反、非 fixture 模式等）
"""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
from typing import Any

from backend.config.settings import CONFIRMABLE_INTAKE_FIELDS, OUTPUT_DIR, run_mode
from backend.orchestrator.graph import build_payload, list_synthetic_cases, run_case


def _print_summary(payload: dict[str, Any]) -> None:
    p = print
    p("─" * 72)
    p(f"案例：{payload['case_id']}　狀態：{payload['state']}　模式：{payload['run_meta']['run_mode']}")
    p(f"聲明：{payload['provenance']['banner']}")
    p("─" * 72)

    screen = payload["screen"]
    dl = screen["deadline"]
    p(f"【程序】期滿日 {dl['deadline'] or '未能計算'}　逾期 {dl['overdue']}　"
      f"77 條款 {screen['art77']['clause'] or '無命中'}")
    p(f"        結論段封鎖：{screen['requires_human_conclusion']}")
    conf = payload.get("intake_confirmed") or []
    p(f"        期間輸入欄位：{'承辦人已確認 ' + str(len(conf)) + ' 欄' if screen.get('procedural_inputs_confirmed') else '未經承辦人確認（' + '、'.join(screen.get('unconfirmed_procedural_fields') or []) + '）'}")
    for s in screen.get("human_conclusion_signals", []):
        p(f"          · 訊號：{s}")
    for i in screen.get("fact_issues", []):
        p(f"          · 爭點 {i['id']}［{i['severity']}］{i['t']}（命中：{'、'.join(i['matched_keywords'])}）")

    p(f"【檢索】法規 {len(payload['retrieval']['laws'])} 筆／"
      f"相似案 {len(payload['retrieval']['cases'])} 筆"
      f"（{payload['retrieval']['retrieval_meta']['similar_case_channel']['label']}）")

    c = payload["citation_counts"]
    p(f"【守門】引用 在庫 {c.get('ok', 0)}／已修正 {c.get('amended', 0)}／"
      f"庫外未驗證 {c.get('out_of_scope', 0)}／查無 {c.get('missing', 0)}")
    st = payload["lamp_stats"]
    p(f"        燈號 綠 {st.get('g', 0)}／黃 {st.get('y', 0)}／紅 {st.get('r', 0)}")
    p(f"        送出：{'允許' if payload['submit_allowed'] else '不得送出'}（blockers {len(payload['blockers'])} 項）"
      f"　※ 實際守門在 POST /api/cases/{{id}}/submit，該端點會重跑一次再判斷（不通過回 409）")
    for b in payload["blockers"]:
        p(f"          ✗ [{b['reason']}] {b['sentence_id']}：{b['detail']}")

    p("【三層誠實】")
    for tier, items in payload["tiers"].items():
        p(f"  {tier}（{len(items)} 項）")
        for it in items[:3]:
            text = (it["t"] or "")[:56]
            p(f"    · [{it['l']}] {text}{'…' if len(it['t'] or '') > 56 else ''}")
        if len(items) > 3:
            p(f"    · …另 {len(items) - 3} 項（完整內容見輸出 JSON）")

    if payload["handoff"].get("questions"):
        p(f"【交接卡】{payload['handoff'].get('note', '')}")
        for q in payload["handoff"]["questions"]:
            p(f"    ? {q}")

    p("【降級】" + (f"{len(payload['run_meta']['degraded'])} 個節點降級" if payload["run_meta"]["degraded"] else "無"))
    for d in payload["run_meta"]["degraded"]:
        p(f"    ⚠ {d['node']}：{d['reason']}")

    if payload["origin_violations"]:
        p("【分層違規】")
        for v in payload["origin_violations"]:
            p(f"    ✗ {v}")
    else:
        p("【分層檢查】通過：每個句子都有 origin，燈號與 why 均非模型產出。")
    p("─" * 72)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python3 -m backend.cli", description="訴願審理 AI 輔助：六節點流程 CLI")
    parser.add_argument("--case", help="合成案例 id（必須是 synthetic- 前綴）")
    parser.add_argument("--list", action="store_true", help="列出可用的合成案例")
    parser.add_argument("--out", default=None, help=f"輸出目錄（預設 {OUTPUT_DIR}）")
    parser.add_argument("--quiet", action="store_true", help="只印 JSON 檔路徑，不印摘要")
    parser.add_argument(
        "--confirm-intake",
        action="store_true",
        help=(
            "模擬承辦人在收文頁確認過全部 intake 欄位（判斷卡 7）。"
            "**不加這個旗標＝沒有人確認過**，此時期間結果不得用來解除結論封鎖，"
            "程序上可直接算出不受理事由的案件也會維持封鎖——那是誠實的預設值。"
        ),
    )
    args = parser.parse_args(argv)

    if args.list or not args.case:
        cases = list_synthetic_cases()
        print("可用的合成案例：")
        for c in cases:
            print(f"  - {c}")
        if not args.case:
            print("\n用法：python3 -m backend.cli --case <synthetic-id>")
            return 0 if args.list else 1
        return 0

    try:
        confirmed = None
        if args.confirm_intake:
            # 先跑一次拿到 N1 抽出來的欄位，再原樣當作「承辦人看過並採用」送進去。
            # 這是模擬，不是真的有人確認——所以它是一個要顯式打開的旗標。
            probe = run_case(args.case)
            confirmed = {
                k: v for k, v in probe.intake.items() if k in CONFIRMABLE_INTAKE_FIELDS
            }
        state = run_case(args.case, confirmed_intake=confirmed)
    except NotImplementedError as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1
    except (FileNotFoundError, ValueError, AssertionError) as e:
        print(f"錯誤：{e}", file=sys.stderr)
        return 1

    payload = build_payload(state)
    out_dir = pathlib.Path(args.out) if args.out else OUTPUT_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{args.case}.output.json"
    out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    if not args.quiet:
        _print_summary(payload)
    print(f"輸出：{out_path}")

    if payload["origin_violations"]:
        print("分層誠實檢查未通過，見上方違規清單。", file=sys.stderr)
        return 1
    if state.state != "VERIFIED":
        print(f"流程未跑到 VERIFIED（停在 {state.state}），請確認是否為預期的人工介入點。", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
