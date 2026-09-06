"""Task 1–7 的單元測試：全部 stdlib，live 分支一律用 monkeypatch 假物件。"""
from __future__ import annotations

import os
import pathlib
import tempfile
from contextlib import contextmanager

import backend.intake.uploads as up
from backend.config import settings
from backend.intake.documents import CJK_RATIO_THRESHOLD, cjk_ratio, route_documents
from backend.intake.uploads import (
    MAX_BYTES,
    list_upload_cases,
    load_upload_case,
    save_upload,
)
from backend.llm import client
from backend.nodes import n1_extract
from backend.orchestrator.graph import (
    digest_from_state,
    list_synthetic_cases,
    load_case,
    run_case,
)
from backend.orchestrator.state import CaseState, NodeCtx
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
