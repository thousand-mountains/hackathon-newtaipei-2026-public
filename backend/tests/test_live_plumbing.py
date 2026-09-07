"""Task 1–7 的單元測試：全部 stdlib，live 分支一律用 monkeypatch 假物件。"""
from __future__ import annotations

import datetime as dt
import os
import pathlib
import tempfile
from contextlib import contextmanager

import backend.intake.uploads as up
import backend.orchestrator.graph as graph_mod
from backend.config import settings
from backend.config.settings import load_snapshot
from backend.engine import deadline as deadline_engine
from backend.intake.documents import CJK_RATIO_THRESHOLD, cjk_ratio, route_documents
from backend.intake.uploads import (
    MAX_BYTES,
    list_upload_cases,
    load_upload_case,
    save_upload,
)
from backend.llm import client
from backend.nodes import n1_extract, n4_retrieval, n5_draft
from backend.orchestrator.graph import (
    digest_from_state,
    list_synthetic_cases,
    load_case,
    run_case,
)
from backend.orchestrator.state import CaseState, NodeCtx
from backend.retrieval.base import Hit
from backend.retrieval.kb import KBRetriever, build_retriever
from backend.tests.harness import assert_eq, assert_in, assert_true


@contextmanager
def env(**kv):
    old = {k: os.environ.get(k) for k in kv}
    for k, v in kv.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def test_settings_defaults_are_offline():
    with env(RUN_MODE=None, MODEL_PROVIDER=None, RETRIEVER=None, KB_MIN_SCORE=None):
        assert_eq(settings.run_mode(), "fixture")
        assert_eq(settings.model_provider(), "bedrock")
        assert_eq(settings.retriever_kind(), "lawtable_only")
        assert_eq(settings.kb_min_score(), 0.25)
        assert_eq(settings.missing_live_settings("fixture", "lawtable_only"), [])


def test_missing_live_settings_names_every_absent_variable():
    with env(BEDROCK_MODEL_ID_EXTRACT=None, BEDROCK_MODEL_ID_DRAFT=None, AWS_REGION=None, BEDROCK_KB_ID=None):
        missing = settings.missing_live_settings("bedrock", "kb")
        for name in ("BEDROCK_MODEL_ID_EXTRACT", "BEDROCK_MODEL_ID_DRAFT", "AWS_REGION", "BEDROCK_KB_ID"):
            assert_in(name, missing)
    with env(BEDROCK_MODEL_ID_EXTRACT="m1", BEDROCK_MODEL_ID_DRAFT="m2", AWS_REGION="r", BEDROCK_KB_ID="k"):
        assert_eq(settings.missing_live_settings("bedrock", "kb"), [])


# ── Task 2：backend/llm/ client ──────────────────────────────────
# 這四個測試全部 monkeypatch `client._invoke_structured`（唯一測試接縫），
# 所以不裝 strands／pydantic 也能跑；跑的是 reshape、值域驗證、cite_ids 白名單與重試語意。


def _fake_extraction() -> dict:
    fields = {
        "no": ("synthetic-1130000001", 0.97), "type": ("違反空氣污染防制法事件", 0.93),
        "person": ("（合成）吳○庭", 0.9), "org": ("新北市政府（合成測資）", 0.95),
        "d1": ("2024-06-11", 0.88), "d2": ("2024-06-13", 0.94), "d3": ("2024-07-20", 0.96),
        "agent": ("無", 0.8), "note": ("訴願人主張未實際收受處分書。", 0.7),
        "service_method": ("deposit", 0.91), "transit_days": (0, 0.85), "interested_party": (False, 0.85),
    }
    return {
        **{k: {"value": v, "conf": c, "quote": f"quote-{k}"} for k, (v, c) in fields.items()},
        "facts_excerpt": [{"text": "訴願人於農地露天燃燒稻稈。", "page": 2, "quote_ref": "synthetic-原處分裁處書.pdf#p2"}],
    }


def test_extract_intake_reshapes_structured_result():
    def fake(system, user, schema_name, tools=None, model_kind="extract", **kw):
        assert_eq(schema_name, "ExtractionResult")
        assert_in("訴願書全文", user)
        return _fake_extraction(), {"input_tokens": 10, "output_tokens": 5}

    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="model-x", AWS_REGION="r"):
            out = client.extract_intake("訴願書全文：……")
    finally:
        client._invoke_structured = orig
    assert_eq(out["intake"]["d2"], "2024-06-13")
    assert_eq(out["conf"]["service_method"], 0.91)
    assert_eq(out["quotes"]["no"], "quote-no")
    assert_eq(len(out["facts_excerpt"]), 1)
    assert_eq(out["model_id"], "model-x")
    assert_eq(out["usage"]["input_tokens"], 10)


def test_extract_intake_rejects_unknown_service_method():
    bad = _fake_extraction()
    bad["service_method"]["value"] = "by_pigeon"
    orig = client._invoke_structured
    client._invoke_structured = lambda *a, **k: (bad, None)
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x")
            except client.LLMError as e:
                assert_in("service_method", str(e))
            else:
                raise AssertionError("未知送達方式必須 raise LLMError")
    finally:
        client._invoke_structured = orig


def test_draft_sentences_filters_cite_ids_outside_context():
    def fake(system, user, schema_name, tools=None, model_kind="draft", **kw):
        assert_eq(schema_name, "DraftResult")
        return {
            "reasoning": [
                {"t": "按訴願法第14條……", "cite_ids": ["L1"], "basis": "訴願法第14條", "source_kind": "law"},
                {"t": "另參最高行 999 判……", "cite_ids": ["L9"], "basis": None, "source_kind": "ref"},
            ],
            "conclusion": [{"t": "訴願不受理。", "cite_ids": ["L4"], "basis": "訴願法第77條", "source_kind": "law"}],
        }, None

    ctx = {"intake": {}, "facts_excerpt": [], "screen": {},
           "laws": [{"id": "L1"}, {"id": "L4"}], "cases": [{"id": "C1"}]}
    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_DRAFT="m", AWS_REGION="r"):
            out = client.draft_sentences(ctx, slots=["reasoning"])
    finally:
        client._invoke_structured = orig
    assert_eq(list(out["slots"].keys()), ["reasoning"], "只回要求的 slot，conclusion 不得出現")
    assert_eq(out["slots"]["reasoning"][0]["cite_ids"], ["L1"])
    assert_eq(out["slots"]["reasoning"][1]["cite_ids"], [], "L9 不在 N4 結果也不在工具命中，必須被清空")
    assert_true(out["slots"]["reasoning"][1].get("unsupported") is True)


def test_client_raises_llm_error_after_retries():
    calls = {"n": 0}

    def boom(*a, **k):
        calls["n"] += 1
        raise RuntimeError("simulated throttling")

    orig = client._invoke_structured
    client._invoke_structured = boom
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x", retries=3, backoff_s=0.0)
            except client.LLMError as e:
                assert_in("simulated throttling", str(e))
            else:
                raise AssertionError("必須 raise LLMError")
    finally:
        client._invoke_structured = orig
    assert_eq(calls["n"], 3)


def test_extract_intake_passes_pdf_documents_as_attachments():
    """Task 3b：掃描件走視覺讀——PDF 原樣往下傳，且提示模型讀附件。"""
    seen: dict = {}

    def fake(system, user, schema_name, tools=None, model_kind="extract", attachments=None, **kw):
        seen["attachments"] = attachments
        seen["user"] = user
        return _fake_extraction(), None

    orig = client._invoke_structured
    client._invoke_structured = fake
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            client.extract_intake("", pdf_documents=[("原處分書.pdf", b"%PDF-1.4")])
    finally:
        client._invoke_structured = orig
    assert_eq(seen["attachments"], [("原處分書.pdf", b"%PDF-1.4")])
    assert_in("請直接閱讀其頁面內容", seen["user"])
    assert_in("（無文字層，請閱讀附件 PDF）", seen["user"])


def test_safe_doc_name_keeps_only_bedrock_allowed_characters():
    """Bedrock document 區塊的 name 只收英數／空白／連字號／括號。"""
    assert_eq(client._safe_doc_name("原處分書.pdf"), "_____pdf")  # 4 個中文字 + 1 個點
    assert_eq(client._safe_doc_name("case (1) [a].pdf"), "case (1) [a]_pdf")
    assert_eq(client._safe_doc_name("原"), "_")
    assert_eq(client._safe_doc_name(""), "doc")
    assert_eq(len(client._safe_doc_name("a" * 200)), 60)


def test_load_model_openai_requires_explicit_model_id():
    """openai 分支不預設任何 model id；缺變數要在 import strands 之前就 raise LLMError。

    檢查排在 import 之前，才會在**沒裝 strands 的機器**上吐出「缺哪個變數」而不是 ModuleNotFoundError。
    """
    with env(MODEL_PROVIDER="openai", OPENAI_API_KEY="k", OPENAI_MODEL_ID=None):
        try:
            client._load_model("extract")
        except client.LLMError as e:
            assert_in("OPENAI_MODEL_ID", str(e))
        else:
            raise AssertionError("缺 OPENAI_MODEL_ID 必須 raise LLMError")


def test_load_model_bedrock_reports_missing_settings_before_importing_strands():
    with env(MODEL_PROVIDER=None, BEDROCK_MODEL_ID_EXTRACT=None, AWS_REGION="r"):
        try:
            client._load_model("extract")
        except client.LLMError as e:
            assert_in("BEDROCK_MODEL_ID_EXTRACT", str(e))
        else:
            raise AssertionError("缺 BEDROCK_MODEL_ID_EXTRACT 必須 raise LLMError")


def test_extract_intake_rejects_non_integer_transit_days_without_retrying():
    """值域錯誤是模型輸出不合格，不是暫時性失敗——直接 LLMError，不燒重試額度。"""
    bad = _fake_extraction()
    bad["transit_days"]["value"] = "七日"
    calls = {"n": 0}

    def once(*a, **k):
        calls["n"] += 1
        return bad, None

    orig = client._invoke_structured
    client._invoke_structured = once
    try:
        with env(BEDROCK_MODEL_ID_EXTRACT="m", AWS_REGION="r"):
            try:
                client.extract_intake("x", retries=3, backoff_s=0.0)
            except client.LLMError as e:
                assert_in("transit_days", str(e))
            else:
                raise AssertionError("非整數在途期間必須 raise LLMError")
    finally:
        client._invoke_structured = orig
    assert_eq(calls["n"], 1, "值域錯誤不得進重試")


# ── Task 3：合成案例 documents ＋ N1 三向分流 ─────────────────────
# bedrock 分支的測試一律 monkeypatch `client.extract_intake`（唯一接縫），
# 所以不裝 strands、沒有憑證也能跑；跑的是分流、卷證餵入與錯誤傳遞。


def _ordinary_fixture():
    return load_case("synthetic-ordinary-01")


def test_synthetic_cases_carry_documents_text():
    for cid in list_synthetic_cases():
        fx = load_case(cid)
        docs = fx.get("documents") or []
        assert_true(len(docs) >= 1, f"{cid} 缺 documents")
        joined = "".join(d["text"] for d in docs)
        for ex in fx["extraction"].get("facts_excerpt", []):
            assert_in(ex["text"], joined, f"{cid} 的 facts_excerpt 必須逐字出現在 documents 內")


def test_n1_bedrock_branch_uses_client_and_marks_origin_llm():
    seen = {}

    def fake_extract(document_text, **kw):
        seen["text"] = document_text
        e = _fake_extraction()
        return {
            "intake": {k: e[k]["value"] for k in client.INTAKE_FIELDS},
            "conf": {k: e[k]["conf"] for k in client.INTAKE_FIELDS},
            "quotes": {k: e[k]["quote"] for k in client.INTAKE_FIELDS},
            "facts_excerpt": e["facts_excerpt"],
            "usage": {"input_tokens": 1, "output_tokens": 1},
            "model_id": "model-x",
        }

    orig = client.extract_intake
    client.extract_intake = fake_extract
    try:
        state = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
        r = n1_extract.run(state, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.extract_intake = orig
    assert_in("寄存於轄區派出所", seen["text"], "卷證全文必須餵給模型")
    assert_eq(state.intake_origin["d2"], "llm")
    assert_eq(state.intake["service_method"], "deposit")
    assert_eq(r.data["generation"]["model_id"], "model-x")
    assert_true(not any("fixture" in log[0] or "重播" in log[0] for log in r.narrative["clerk"]["logs"]),
                "bedrock 分支的敘述不得再說自己是重播")


def test_n1_bedrock_branch_propagates_llm_error():
    orig = client.extract_intake
    client.extract_intake = lambda *a, **k: (_ for _ in ()).throw(client.LLMError("boom"))
    try:
        try:
            n1_extract.run(CaseState(case_id="x", run_mode="bedrock"), NodeCtx(run_mode="bedrock"),
                           case_fixture=_ordinary_fixture())
        except client.LLMError:
            pass
        else:
            raise AssertionError("LLMError 必須往上拋，不得吞掉改吐 fixture")
    finally:
        client.extract_intake = orig


def test_n1_still_raises_for_unknown_mode():
    try:
        n1_extract.run(CaseState(case_id="x", run_mode="local"), NodeCtx(run_mode="local"), case_fixture=_ordinary_fixture())
    except NotImplementedError:
        pass
    else:
        raise AssertionError("local 模式仍未實作，必須 raise")


# ── Task 3b：卷證文字路由、上傳案件、N2/N3 digest 來源 ─────────────
# route_documents 的 PDF 文字抽取一律用注入的 extractor，測試不依賴機器上有沒有 pdftotext。


def test_cjk_ratio_and_routing_with_injected_extractor():

    assert_true(cjk_ratio("訴願人於農地露天燃燒") > 0.9)
    assert_true(cjk_ratio("abc def 123") == 0.0)
    d = pathlib.Path(tempfile.mkdtemp())
    (d / "a.txt").write_text("訴願書全文（合成）", encoding="utf-8")
    (d / "digital.pdf").write_bytes(b"%PDF-1.4 fake")
    (d / "scan.pdf").write_bytes(b"%PDF-1.4 fake")

    def extractor(p):
        return "裁處書（合成）本文" if p.name == "digital.pdf" else "\x0c\x0c   "

    docs = route_documents(d, text_extractor=extractor)
    kinds = {x.n: x.kind for x in docs}
    assert_eq(kinds, {"a.txt": "txt", "digital.pdf": "pdf_text", "scan.pdf": "pdf_visual"})
    assert_true(all(x.cjk_ratio >= CJK_RATIO_THRESHOLD for x in docs if x.kind != "pdf_visual"))


def test_save_and_load_upload_case_shape_and_prefix():

    d = pathlib.Path(tempfile.mkdtemp())
    meta = save_upload([("訴願書.txt", "訴願書全文（合成）".encode("utf-8"))], uploads_dir=d)
    assert_true(meta["case_id"].startswith("upload-"))
    assert_eq(meta["provenance"]["kind"], "uploaded")
    fx = load_upload_case(meta["case_id"], uploads_dir=d)
    assert_eq(fx["id"], meta["case_id"])
    assert_eq([f["n"] for f in fx["files"]], ["訴願書.txt"])
    assert_eq(fx["documents"][0]["kind"], "txt")
    assert_in("訴願書全文", fx["documents"][0]["text"])
    assert_true("extraction" not in fx and "draft_fixture" not in fx, "上傳案沒有 fixture 區塊")
    assert_eq(list_upload_cases(uploads_dir=d), [meta["case_id"]])


def test_save_upload_rejects_bad_suffix_and_oversize():

    d = pathlib.Path(tempfile.mkdtemp())
    for files in ([("x.docx", b"1")], [("x.pdf", b"0" * (MAX_BYTES + 1))]):
        try:
            save_upload(files, uploads_dir=d)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{files[0][0]} 必須被拒絕")


def test_load_case_dispatches_on_prefix():
    assert_eq(load_case("synthetic-ordinary-01")["id"], "synthetic-ordinary-01")
    for bad in ("real-123", "upload-does-not-exist"):
        try:
            load_case(bad)
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError(f"{bad} 必須 raise")


def test_upload_case_in_fixture_mode_is_refused_honestly():

    d = pathlib.Path(tempfile.mkdtemp())
    meta = save_upload([("訴願書.txt", "訴願書全文（合成）".encode("utf-8"))], uploads_dir=d)
    orig = up.UPLOADS_DIR
    up.UPLOADS_DIR = d
    try:
        try:
            run_case(meta["case_id"], mode="fixture")
        except ValueError as e:
            assert_in("bedrock", str(e), "要說清楚上傳案只能在 bedrock 模式跑")
        else:
            raise AssertionError("上傳案在 fixture 模式沒有 extraction 可重播，必須 raise")
    finally:
        up.UPLOADS_DIR = orig


def test_n1_bedrock_passes_pdf_visual_docs_as_attachments():
    seen = {}

    def fake_extract(document_text, *, pdf_documents=None, **kw):
        seen["text"] = document_text
        seen["pdfs"] = pdf_documents
        e = _fake_extraction()
        return {
            "intake": {k: e[k]["value"] for k in client.INTAKE_FIELDS},
            "conf": {k: e[k]["conf"] for k in client.INTAKE_FIELDS},
            "quotes": {},
            "facts_excerpt": e["facts_excerpt"],
            "usage": None,
            "model_id": "m",
        }

    fixture = {
        "id": "upload-x", "files": [], "provenance": {"kind": "uploaded"},
        "documents": [
            {"n": "a.txt", "kind": "txt", "text": "訴願書全文", "cjk_ratio": 1.0, "path": None},
            {"n": "scan.pdf", "kind": "pdf_visual", "text": "", "cjk_ratio": 0.0, "bytes": b"%PDF-1.4 fake"},
        ],
    }
    orig = client.extract_intake
    client.extract_intake = fake_extract
    try:
        r = n1_extract.run(
            CaseState(case_id="upload-x", run_mode="bedrock"), NodeCtx(run_mode="bedrock"), case_fixture=fixture
        )
    finally:
        client.extract_intake = orig
    assert_in("訴願書全文", seen["text"])
    # 檔名加序號前綴：多份中文檔名 PDF 會被 _safe_doc_name 清成同一個名字，序號讓模型分得出來
    assert_eq(seen["pdfs"], [("2-scan.pdf", b"%PDF-1.4 fake")])
    assert_eq(r.data["generation"]["input_route"], {"a.txt": "txt", "scan.pdf": "pdf_visual"})
    assert_true(any("視覺" in log[0] for log in r.narrative["clerk"]["logs"]), "視覺讀取要在敘述裡講出來")


def test_n2_n3_digest_falls_back_to_n1_output():
    st = CaseState(case_id="upload-x", run_mode="bedrock")
    st.facts_excerpt = [{"text": "訴願人於農地露天燃燒稻稈。"}, {"text": "經稽查查獲。"}]
    st.intake = {"note": "主張未收受處分書"}
    d = digest_from_state(st)
    assert_in("露天燃燒稻稈", d)
    assert_in("經稽查查獲", d)
    assert_in("主張未收受", d)


def test_save_upload_refuses_colliding_filenames():
    """兩份檔案清成同一個檔名時必須拒絕，不能後者默默蓋掉前者。

    `_safe_name` 只取 basename，所以「卷證/訴願書.txt」與「訴願書.txt」會落在同一個路徑：
    覆蓋之後磁碟上只剩一份，但 `case.json` 的 `files[]` 仍宣稱有兩份——
    那就是對承辦人虛報「這些卷證我都收到了」。寧可擋下來請人改名。
    """
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        save_upload([("卷證/訴願書.txt", "甲（合成測資）".encode("utf-8")),
                     ("訴願書.txt", "乙（合成測資）".encode("utf-8"))], uploads_dir=d)
    except ValueError as e:
        assert_in("訴願書.txt", str(e), "要講出是哪個檔名撞在一起")
    else:
        raise AssertionError("同名檔案會互相覆蓋、files[] 會虛報份數，必須拒絕")


def test_n1_bedrock_refuses_pdf_visual_without_bytes():
    """pdf_visual 卻沒有 bytes：直接 raise，不要送一份空的 PDF 給模型。

    送 b"" 過去的話，模型「讀」的是一份不存在的文件，回來的欄位會被記成
    input_route 裡的 pdf_visual——等於宣稱視覺讀過了。這是分層誠實違規（CONSTITUTION §1）。
    """
    calls = {"n": 0}

    def fake_extract(document_text, *, pdf_documents=None, **kw):
        calls["n"] += 1
        raise AssertionError("空的 pdf_visual 不該走到模型呼叫")

    fixture = {
        "id": "upload-y", "files": [], "provenance": {"kind": "uploaded"},
        "documents": [{"n": "scan.pdf", "kind": "pdf_visual", "text": "", "cjk_ratio": 0.0}],
    }
    orig = client.extract_intake
    client.extract_intake = fake_extract
    try:
        n1_extract.run(
            CaseState(case_id="upload-y", run_mode="bedrock"), NodeCtx(run_mode="bedrock"), case_fixture=fixture
        )
    except ValueError as e:
        assert_in("scan.pdf", str(e), "要指名是哪一份卷證缺內容")
    else:
        raise AssertionError("pdf_visual 缺 bytes 必須 raise，不得送空檔案")
    finally:
        client.extract_intake = orig
    assert_eq(calls["n"], 0, "不該呼叫到模型")


# ── Task 4：N5 bedrock 分支（含 retrieve 工具介面）─────────────────
# 一律 monkeypatch `client.draft_sentences`（節點端唯一接縫），所以不裝 strands、
# 沒有憑證也能跑；跑的是分流、槽位封鎖、工具接線與 refs 落地。


def _screened_state(requires_human: bool) -> CaseState:
    # deadline 直接用期間引擎的真實輸出：`steps` 的形狀（rule／value／basis）是
    # build_doc_skeleton 的硬需求，手寫一份簡化版會讓測試在假的形狀上通過。
    computed = deadline_engine.compute(
        service_method="deposit", service_date=dt.date(2024, 6, 13),
        filing_date=dt.date(2024, 7, 20), transit_days=0, interested_party=False,
    ).as_dict()
    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.intake = {"no": "synthetic-1130000001", "type": "違反空氣污染防制法事件", "d2": "2024-06-13", "d3": "2024-07-20"}
    st.facts_excerpt = [{"text": "事實段。", "page": 1}]
    st.screen = {"requires_human_conclusion": requires_human,
                 "deadline": computed,
                 "art77": {"clause": "77-2"}}
    st.retrieval = {"laws": [{"id": "L1", "t": "訴願法第14條"}, {"id": "L4", "t": "訴願法第77條"}],
                    "cases": [{"id": "C1", "t": "113年-…-駁回", "outcome": "駁回"}], "retrieval_meta": {}}
    return st


def test_n5_bedrock_branch_requests_only_allowed_slots_and_keeps_shape():
    seen = {}

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        seen["slots"] = list(slots)
        seen["ctx_ids"] = [law["id"] for law in context["laws"]] + [c["id"] for c in context["cases"]]
        seen["retrieve_fn"] = retrieve_fn
        return {"slots": {s: [{"t": f"{s} 句", "cite_ids": ["L1"], "basis": "訴願法第14條", "source_kind": "law"}] for s in slots},
                "tool_calls": [], "usage": None, "model_id": "model-d"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        r = n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_eq(seen["slots"], ["reasoning"], "封鎖時 conclusion 不得進 slots")
    assert_eq(seen["ctx_ids"], ["L1", "L4", "C1"])
    assert_true(seen["retrieve_fn"] is None, "ctx.retriever 為 None 時不給工具")
    assert_eq(st.draft["generation_mode"], "bedrock_live")
    assert_eq(st.draft["model_id"], "model-d")
    assert_eq(list(st.draft["slots"].keys()), ["reasoning"])
    assert_true("l" not in st.draft["slots"]["reasoning"][0] and "why" not in st.draft["slots"]["reasoning"][0])
    assert_true(r.degraded is False, "真模型生成不是降級")


def test_n5_bedrock_branch_strips_conclusion_even_if_model_returns_it():
    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {"slots": {"reasoning": [{"t": "理由", "cite_ids": [], "basis": None, "source_kind": "law"}],
                          "conclusion": [{"t": "訴願不受理。", "cite_ids": [], "basis": None, "source_kind": "law"}]},
                "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_true("conclusion" not in st.draft["slots"], "程式端第二道：模型多回的 conclusion 必須刪掉")
    assert_true(st.draft["conclusion_dropped"] is True)


def test_n5_bedrock_branch_wires_retriever_into_tool():
    class FakeRetriever:
        name = "fake_kb"

        def __init__(self):
            self.queries = []

        def search(self, query, filters=None, top_k=5):
            self.queries.append((query, filters))
            return [Hit(id="R1", title="法務部 93 函釋", score=0.9, source="行政函釋/法務部93.txt",
                        payload={"text": "寄存之日視為收受送達之日"})]

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        hits = retrieve_fn("寄存送達 生效")
        assert_eq(hits[0]["id"], "R1")
        return {"slots": {"reasoning": [{"t": "依函釋……", "cite_ids": ["R1"], "basis": None, "source_kind": "ref"}]},
                "tool_calls": [{"query": "寄存送達 生效", "hit_ids": ["R1"]}], "usage": None, "model_id": "m"}

    fr = FakeRetriever()
    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock", retriever=fr), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    assert_eq(fr.queries[0][1]["prefix"], ["行政函釋/", "司法院釋字及行政判解/"], "N5 的工具只准查函釋與判解前綴")
    assert_eq(st.draft["refs"][0]["id"], "R1")
    assert_eq(st.draft["refs"][0]["src"], "行政函釋/法務部93.txt")


def test_draft_sentences_resets_tool_state_between_retries():
    """重試是「重新來過」，不是「接著上一次」。

    `tool_calls` 與 `allowed` 是 draft_sentences 的閉包狀態，被工具就地累加。
    若不在每次嘗試前重設，重試回來的 `tool_calls` 會混進上一次失敗嘗試的紀錄
    （報告上會虛報查了幾次），白名單也會留著上一輪命中的 id——那等於讓模型引用
    一批「這次沒查到」的來源，違反引用必可驗（CONSTITUTION §2）。

    strands 未安裝時 `client.tool` 是 None，這裡用 identity decorator 頂上，
    讓 `tools[0]` 就是未包裝的 retrieve_refs，測試才拿得到工具本體來呼叫。
    """
    attempts = {"n": 0}

    def fake_invoke(system, user, schema_name, tools=None, model_kind="draft", **kw):
        attempts["n"] += 1
        tools[0](f"第{attempts['n']}次查詢")
        if attempts["n"] == 1:
            raise RuntimeError("simulated throttling")
        return {"reasoning": [{"t": "依函釋……", "cite_ids": ["R1", "R9"], "basis": None, "source_kind": "ref"}]}, None

    def retrieve_fn(query):
        rid = "R9" if "第1次" in query else "R1"
        return [{"id": rid, "src": "行政函釋/x.txt", "text": "……"}]

    ctx = {"intake": {}, "facts_excerpt": [], "screen": {}, "laws": [{"id": "L1"}], "cases": []}
    orig_invoke, orig_tool = client._invoke_structured, client.tool
    client._invoke_structured = fake_invoke
    client.tool = lambda f: f
    try:
        with env(BEDROCK_MODEL_ID_DRAFT="m", AWS_REGION="r"):
            out = client.draft_sentences(ctx, ["reasoning"], retrieve_fn, retries=3, backoff_s=0.0)
    finally:
        client._invoke_structured = orig_invoke
        client.tool = orig_tool
    assert_eq(attempts["n"], 2)
    assert_eq(out["tool_calls"], [{"query": "第2次查詢", "hit_ids": ["R1"]}], "第一次嘗試的工具紀錄不得殘留")
    assert_eq(out["slots"]["reasoning"][0]["cite_ids"], ["R1"], "R9 是上一次失敗嘗試命中的，白名單必須已重設")
    assert_true(out["slots"]["reasoning"][0].get("unsupported") is True)


def test_n5_renumbers_ref_ids_so_two_tool_calls_cannot_collide():
    """ref id 一律由 N5 重編：檢索器兩次都回 id="R1" 時，兩筆必須是 R1／R2。

    沿用檢索器的 id 會讓 `refs` 兩筆不同來源共用一個號碼，模型引 R1 時對不出是哪一筆
    ——引用必可驗（CONSTITUTION §2）的直接破口。原始 id 留在 `src_id`，仍能追回檢索器那端。
    """
    class CollidingRetriever:
        name = "fake_kb"

        def __init__(self):
            self.n = 0

        def search(self, query, filters=None, top_k=5):
            self.n += 1
            return [Hit(id="R1", title=f"來源 {self.n}", score=0.9,
                        source=f"行政函釋/第{self.n}份.txt", payload={"text": f"段落 {self.n}"})]

    def fake_draft(context, slots, retrieve_fn=None, **kw):
        first = retrieve_fn("寄存送達 生效")
        second = retrieve_fn("回復原狀 不可歸責")
        assert_eq([h["id"] for h in first], ["R1"])
        assert_eq([h["id"] for h in second], ["R2"], "第二次工具呼叫要接著編號，不得從頭來過")
        assert_eq(second[0]["src_id"], "R1", "回給模型的 dict 也要帶得回原始 id")
        return {"slots": {"reasoning": [{"t": "依函釋……", "cite_ids": ["R2"], "basis": None, "source_kind": "ref"}]},
                "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        n5_draft.run(st, NodeCtx(run_mode="bedrock", retriever=CollidingRetriever()),
                     case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    refs = st.draft["refs"]
    assert_eq([r["id"] for r in refs], ["R1", "R2"], "兩筆不同來源不得共用一個 ref id")
    assert_eq([r["src_id"] for r in refs], ["R1", "R1"], "原始 id 要留著，才追得回檢索器那端")
    assert_eq([r["src"] for r in refs], ["行政函釋/第1份.txt", "行政函釋/第2份.txt"])


def test_n5_logs_when_upstream_retrieval_is_empty():
    """N4 什麼都沒查到時，模型的引用一定全被清空——畫面上要看得出原因出在上游。"""
    def fake_draft(context, slots, retrieve_fn=None, **kw):
        return {"slots": {"reasoning": [{"t": "理由", "cite_ids": [], "basis": None, "source_kind": "law"}]},
                "tool_calls": [], "usage": None, "model_id": "m"}

    orig = client.draft_sentences
    client.draft_sentences = fake_draft
    try:
        st = _screened_state(requires_human=True)
        st.retrieval = {"laws": [], "cases": [], "retrieval_meta": {}}
        r = n5_draft.run(st, NodeCtx(run_mode="bedrock"), case_fixture=_ordinary_fixture())
    finally:
        client.draft_sentences = orig
    logs = [row[0] for row in r.narrative["draft"]["logs"]]
    assert_true(any("上游檢索結果為空" in text for text in logs), f"缺空檢索的說明 log：{logs}")


# ── Task 5：Managed KB 檢索器與 N4 通道 B ────────────────────────────
class _FakeBedrockAgentRuntime:
    """模擬 boto3 bedrock-agent-runtime client 的 retrieve()。"""

    def __init__(self, results):
        self.results = results
        self.calls = []

    def retrieve(self, **kw):
        self.calls.append(kw)
        return {"retrievalResults": self.results}


def _kb_result(uri_tail, score, text, file_type="TXT"):
    return {"score": score,
            "content": {"text": text},
            "location": {"s3Location": {"uri": f"s3://bucket/kb/official/{uri_tail}"}},
            "metadata": {"_file_type": file_type, "_source_uri": f"s3://bucket/kb/official/{uri_tail}"}}


def test_kb_retriever_filters_by_prefix_score_filetype_and_exclusion():
    fake = _FakeBedrockAgentRuntime([
        _kb_result("歷史訴願決定書/113年/16.113年-違反洗錢防制法事件-79I-駁回.txt", 0.9, "主文：訴願駁回。"),
        _kb_result("歷史訴願決定書/114年/1131030896-撤銷.txt", 0.8, "撤銷"),          # exclude_case 命中
        _kb_result("行政函釋/法務部93.txt", 0.95, "函釋"),                              # 前綴不符
        _kb_result("歷史訴願決定書/112年/低分.txt", 0.1, "低分"),                       # 分數不足
        _kb_result("歷史訴願決定書/112年/舊PDF.pdf", 0.9, "亂碼", file_type="PDF"),      # PDF 一律丟
    ])
    r = KBRetriever(kb_id="kb-x", region="r", min_score=0.25, client=fake)
    hits = r.search("露天燃燒", filters={"prefix": ["歷史訴願決定書/"], "exclude_case": "1131030896"}, top_k=5)
    assert_eq([h.source for h in hits], ["歷史訴願決定書/113年/16.113年-違反洗錢防制法事件-79I-駁回.txt"])
    assert_eq(hits[0].payload["outcome"], "駁回")
    assert_eq(hits[0].payload["provenance"], "official")
    assert_true(hits[0].verified is False, "verified 由 N6 對 manifest 決定，檢索不自己宣稱")
    call = fake.calls[0]
    assert_eq(call["knowledgeBaseId"], "kb-x")
    assert_eq(call["retrievalConfiguration"]["managedSearchConfiguration"]["numberOfResults"], 15, "多抓三倍再後過濾")


def test_kb_retriever_parses_public_prefix_and_outcome_from_filename():
    res = _kb_result("x", 0.9, "t")
    res["location"]["s3Location"]["uri"] = "s3://b/kb/public/新北訴願決定書_全量/1121051256_不受理.txt"
    res["metadata"]["_source_uri"] = res["location"]["s3Location"]["uri"]
    r = KBRetriever(kb_id="k", region="r", client=_FakeBedrockAgentRuntime([res]))
    hits = r.search("q", filters={"prefix": ["新北訴願決定書_全量/"]})
    assert_eq(hits[0].payload["provenance"], "public_crawl")
    assert_eq(hits[0].payload["outcome"], "不受理")


def test_build_retriever_requires_env_for_kb():
    with env(BEDROCK_KB_ID=None, AWS_REGION=None):
        try:
            build_retriever("kb")
        except ValueError as e:
            assert_in("BEDROCK_KB_ID", str(e))
        else:
            raise AssertionError("缺變數必須 raise")
    assert_true(build_retriever("lawtable_only") is None)


def test_n4_uses_injected_retriever_for_similar_cases():
    class FakeKB:
        name = "bedrock_kb"

        def __init__(self):
            self.queries = []

        def search(self, query, filters=None, top_k=5):
            self.queries.append((query, filters, top_k))
            return [Hit(id="kb-1", title="113年-違反空氣污染防制法事件-駁回", score=0.88,
                        source="歷史訴願決定書/113年/x-駁回.txt",
                        payload={"outcome": "駁回", "provenance": "official", "text": "主文：訴願駁回。"})]

        def meta(self):
            return {"backend": "bedrock_kb", "available": True}

    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.intake = {"type": "違反空氣污染防制法事件", "note": "主張未收受"}
    st.facts_excerpt = [{"text": "訴願人於農地露天燃燒稻稈。", "page": 2}]
    st.classification = {"class": {"case_type": "違反空氣污染防制法事件", "law_hits": ["空氣污染防制法"]}}
    st.screen = {"art77": {"clause": "77-2"}, "deadline": {"steps": []}}
    kb = FakeKB()
    r = n4_retrieval.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot(), retriever=kb))
    assert_eq(len(st.retrieval["cases"]), 1)
    c = st.retrieval["cases"][0]
    assert_eq(c["id"], "C1")
    assert_eq(c["outcome"], "駁回")
    assert_eq(c["src"], "歷史訴願決定書/113年/x-駁回.txt")
    assert_eq(c["origin"], "retrieval")
    assert_true(c["lamp"] is None, "燈號歸 N6")
    assert_in("露天燃燒稻稈", kb.queries[0][0], "查詢句必須含事實段原文")
    assert_eq(kb.queries[0][1]["prefix"], ["歷史訴願決定書/"])
    assert_eq(st.retrieval["retrieval_meta"]["backend"], "lawtable+bedrock_kb")
    assert_true(r.degraded is False, "兩條通道都有結果就不是降級")


def test_n4_without_retriever_keeps_phase0_behaviour():
    st = CaseState(case_id="x", run_mode="fixture")
    st.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    st.screen = {"art77": {}, "deadline": {"steps": []}}
    r = n4_retrieval.run(st, NodeCtx(run_mode="fixture", snapshot=load_snapshot()))
    assert_eq(st.retrieval["cases"], [])
    assert_eq(st.retrieval["retrieval_meta"]["backend"], "lawtable_only")
    assert_true(r.degraded is True)


def test_n4_result_distribution_survives_unlabelled_outcome():
    """檔名讀不出主文時 `outcome` 是 None——結果分布那行不得因為 None 混字串而炸掉。

    這條釘的是 `_count(c['outcome'] or '未標示' ...)` 那個 `or`：拿掉它，
    `sorted()` 會在 None 與 '駁回' 之間比大小，`TypeError` 直接打死整個 N4。
    「未標示」是明講讀不出來，不是替它猜一個結果（CONSTITUTION §2）。
    """
    class MixedKB:
        name = "bedrock_kb"

        def search(self, query, filters=None, top_k=5):
            return [
                Hit(id="kb-1", title="113年-違反建築法事件-駁回", score=0.9,
                    source="歷史訴願決定書/113年/a-駁回.txt",
                    payload={"outcome": "駁回", "provenance": "official", "text": "主文：訴願駁回。"}),
                Hit(id="kb-2", title="113年-違反建築法事件", score=0.7,
                    source="歷史訴願決定書/113年/b.txt",
                    payload={"outcome": None, "provenance": "official", "text": "（檔名不含結果字樣）"}),
            ]

        def meta(self):
            return {"backend": "bedrock_kb", "available": True}

    st = CaseState(case_id="synthetic-ordinary-01", run_mode="bedrock")
    st.facts_excerpt = [{"text": "訴願人擅自變更建物使用。", "page": 1}]
    st.classification = {"class": {"case_type": "違反建築法事件", "law_hits": ["建築法"]}}
    st.screen = {"art77": {}, "deadline": {"steps": []}}
    r = n4_retrieval.run(st, NodeCtx(run_mode="bedrock", snapshot=load_snapshot(), retriever=MixedKB()))
    assert_eq(len(st.retrieval["cases"]), 2)
    assert_true(st.retrieval["cases"][1]["outcome"] is None, "讀不出主文就留 None，不猜一個結果")
    dist = [row[0] for row in r.narrative["case"]["logs"] if row[0].startswith("結果分布：")]
    assert_eq(len(dist), 1, f"結果分布那行不見了：{r.narrative['case']['logs']}")
    assert_in("駁回 1 件", dist[0])
    assert_in("未標示 1 件", dist[0])


def test_run_case_passes_fixture_exclude_case_into_build_retriever():
    """demo 案不得檢索到自己的來源決定書——這條釘住 fixture → graph → build_retriever 的傳遞。

    假的 build_retriever 回 None（等同 lawtable_only），所以不需要 boto3、
    N4 通道 B 的行為也不變；被釘住的只有「參數有沒有真的傳過去」。
    """
    calls: list[tuple[str, str | None]] = []

    def fake_build(kind, *, exclude_case=None):
        calls.append((kind, exclude_case))
        return None

    orig = graph_mod.build_retriever
    graph_mod.build_retriever = fake_build
    try:
        with env(RETRIEVER=None):
            graph_mod.run_case("synthetic-ordinary-01", mode="fixture")
    finally:
        graph_mod.build_retriever = orig

    assert_eq(len(calls), 1, "run_case 必須向編排層要一次 retriever")
    assert_eq(calls[0][0], "lawtable_only", "kind 來自 settings.retriever_kind()")
    assert_eq(calls[0][1], "synthetic-src-0000000001", "fixture 的 exclude_case 沒有傳到檢索器")
