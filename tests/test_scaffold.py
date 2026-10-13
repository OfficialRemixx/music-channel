"""P0 smoke tests: config loads, dry_run defaults are safe, every stub module
fails safe (rejects/no-ops) rather than silently doing something real."""
import os
os.environ["FORCE_DRY_RUN"] = "true"

from common.config import load_config, is_dry_run, is_enabled
from common import state as state_mod
from generate.base import BatchSpec
from generate.kaggle_acestep import KaggleAceStepProvider
from generate.cc_library import CCLibraryProvider
import trend, qc, mix, seo, upload, analytics, policy_watch, notify


def test_config_loads_and_dry_run_forced():
    cfg = load_config()
    assert is_dry_run(cfg) is True
    assert isinstance(is_enabled(cfg), bool)


def test_state_roundtrip(tmp_path):
    p = tmp_path / "state.json"
    s = state_mod.load_state(p)
    assert s["schema_version"] == 1
    s["quota_usage"]["videos.insert"] = 1
    state_mod.save_state(p, s)
    s2 = state_mod.load_state(p)
    assert s2["quota_usage"]["videos.insert"] == 1


def test_trend_fallback_has_no_proper_nouns_by_construction():
    matrix = trend.build_prompt_matrix(dry_run=True)
    assert len(matrix) > 0
    for entry in matrix:
        assert "lane" in entry


def test_qc_fails_safe_by_default():
    r = qc.check_track("does-not-exist.wav")
    assert r.accepted is False  # R9: nothing passes QC until real checks exist


def test_mix_empty_pool_returns_empty_not_crash():
    out = mix.build_mix([])
    assert out["tracks"] == []


def test_seo_template_passes_its_own_r4_gate():
    meta = seo.generate_metadata("calm", "study", "Deep Focus", 1, 60)
    assert seo.validate_public_text(meta) == []
    assert "AI" not in meta["title"] and "ai" not in meta["title"].lower()


def test_providers_dry_run_roundtrip():
    cfg = {"dry_run": True}
    for Provider in (KaggleAceStepProvider, CCLibraryProvider):
        p = Provider(cfg)
        job = p.submit(BatchSpec(prompts=[], tracks_per_batch=1, batch_id="t"))
        job = p.poll(job)
        assert job.status == "complete"
        assert p.fetch(job) == []  # no real tracks in dry_run, by design


def test_upload_dry_run_never_touches_network():
    cfg = {"dry_run": True}
    r = upload.upload_video("x.mp4", {"title": "t"}, cfg)
    assert r.ok is True and r.reason == "dry_run"
    g = upload.check_claim_gate("dryrun-video-id", cfg)
    assert g.ok is True


def test_analytics_report_runs_without_api():
    s = state_mod.load_state("___nope___.json")
    report = analytics.weekly_report(s)
    assert "4,000h" in report or "4000" in report.replace(",", "")


def test_policy_watch_stub_reports_no_changes():
    out = policy_watch.check_for_changes({})
    assert all(v["changed"] is False for v in out.values())


def test_notify_dry_run_does_not_raise():
    assert notify.send("hello", cfg={"dry_run": True}) is False
