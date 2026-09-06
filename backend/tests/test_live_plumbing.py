"""Task 1–7 的單元測試：全部 stdlib，live 分支一律用 monkeypatch 假物件。"""
from __future__ import annotations

import os
from contextlib import contextmanager

from backend.config import settings
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
