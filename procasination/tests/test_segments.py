from __future__ import annotations

from procasination.capture import ActivitySample
from procasination.config import ProcrastinationSettings
from procasination.segments import Segmenter, VerdictCache, prefilter


def sample(app: str, title: str = "", url: str = "", idle: int = 0, at: str = "2026-09-19T14:00:00+00:00",
           sensitive: bool = False) -> ActivitySample:
    return ActivitySample(captured_at=at, app=app, window_title=title, url=url,
                          idle_seconds=idle, sensitive=sensitive)


def test_firefox_forks_are_recognised_as_browsers_without_a_url():
    # Zen and Firefox expose no scriptable tab URL on macOS.
    assert sample("zen", "(248) YouTube").is_browser is True
    assert sample("Firefox", "YouTube").is_browser is True
    assert sample("Google Chrome", "YouTube").is_browser is True
    assert sample("Preview", "ps3.pdf").is_browser is False


def test_repeated_context_collapses_into_one_segment():
    segmenter = Segmenter()
    for step in range(4):
        segmenter.add(sample("Preview", "ps3.pdf", at="2026-09-19T14:00:%02d+00:00" % (step * 15)))

    pending = segmenter.drain()
    assert len(pending) == 1
    assert pending[0].sample_count == 4
    # Four samples 15s apart account for a full minute of observed time.
    assert pending[0].duration_seconds(15) == 60


def test_a_single_sample_counts_as_one_interval():
    segmenter = Segmenter()
    segmenter.add(sample("Preview", "ps3.pdf"))
    assert segmenter.drain()[0].duration_seconds(15) == 15


def test_switching_app_closes_the_previous_segment():
    segmenter = Segmenter()
    segmenter.add(sample("Preview", "ps3.pdf", at="2026-09-19T14:00:00+00:00"))
    segmenter.add(sample("Steam", "Store", at="2026-09-19T14:00:15+00:00"))

    pending = segmenter.drain()
    assert [segment.app for segment in pending] == ["Preview", "Steam"]


def test_live_segment_is_reported_every_cycle_while_still_open():
    segmenter = Segmenter()
    segmenter.add(sample("Steam", "Store"))

    assert len(segmenter.drain()) == 1
    # Drift has to be catchable before the student switches away from it.
    assert len(segmenter.drain()) == 1


def test_title_and_site_distinguish_segments_on_the_same_app():
    segmenter = Segmenter()
    segmenter.add(sample("Google Chrome", "Integration by parts", "https://youtube.com/watch?a"))
    segmenter.add(sample("Google Chrome", "Minecraft 100 days", "https://youtube.com/watch?b"))

    assert len(segmenter.drain()) == 2


def test_a_ticking_unread_count_does_not_look_like_a_new_context():
    # "(248) YouTube" becomes "(249) YouTube" on its own while the page sits
    # untouched. Treating that as new context would re-bill every notification.
    segmenter = Segmenter()
    segmenter.add(sample("zen", "(248) YouTube"))
    segmenter.add(sample("zen", "(249) YouTube"))
    segmenter.add(sample("zen", "[250] YouTube"))

    pending = segmenter.drain()
    assert len(pending) == 1
    assert pending[0].sample_count == 3


def test_the_unread_count_is_stripped_from_what_the_model_sees():
    segmenter = Segmenter()
    segmenter.add(sample("zen", "(248) YouTube"))
    assert segmenter.drain()[0].describe()["window_title"] == "YouTube"


def test_a_real_number_in_a_title_is_kept():
    # Only a leading bracketed count is decoration; "Lecture 7" is content.
    segmenter = Segmenter()
    segmenter.add(sample("Preview", "Lecture 7 (2026) notes.pdf"))
    assert segmenter.drain()[0].describe()["window_title"] == "Lecture 7 (2026) notes.pdf"


def test_url_host_ignores_www_and_path():
    segmenter = Segmenter()
    segmenter.add(sample("Google Chrome", "Overleaf", "https://www.overleaf.com/project/123"))
    assert segmenter.drain()[0].host == "overleaf.com"


class TestPrefilter:
    settings = ProcrastinationSettings()

    def _one(self, activity: ActivitySample):
        segmenter = Segmenter()
        segmenter.add(activity)
        return prefilter(segmenter.drain()[0], self.settings)

    def test_idle_beyond_the_threshold_is_off_task_without_a_model_call(self):
        verdict = self._one(sample("Finder", "Desktop", idle=700))
        assert verdict["verdict"] == "off_task"
        assert verdict["decided_by"] == "prefilter"
        assert "11 minutes" in verdict["reason"]

    def test_a_few_minutes_of_reading_without_input_is_not_slacking(self):
        # Reading, thinking, and watching a lecture all look idle to the OS.
        assert self._one(sample("Safari", "Chapter 4 notes", idle=300)) is None

    def test_a_study_tool_is_on_task(self):
        assert self._one(sample("Visual Studio Code", "main.py"))["verdict"] == "on_task"

    def test_a_game_launcher_is_off_task(self):
        assert self._one(sample("Steam", "Library"))["verdict"] == "off_task"

    def test_an_entertainment_host_is_off_task(self):
        verdict = self._one(sample("Google Chrome", "For You", "https://www.tiktok.com/foryou"))
        assert verdict["verdict"] == "off_task"

    def test_a_coursework_host_is_on_task(self):
        verdict = self._one(sample("Google Chrome", "Project", "https://www.overleaf.com/project/1"))
        assert verdict["verdict"] == "on_task"

    def test_a_private_app_is_never_judged(self):
        verdict = self._one(sample("1Password", "", sensitive=True))
        assert verdict["verdict"] == "ambiguous"
        assert verdict["needs_visual"] is False

    def test_an_untitled_browser_window_skips_straight_to_a_screenshot(self):
        verdict = self._one(sample("zen", ""))
        assert verdict["verdict"] == "ambiguous"
        assert verdict["needs_visual"] is True
        assert verdict["decided_by"] == "prefilter"

    def test_a_titled_browser_window_still_goes_to_the_model(self):
        assert self._one(sample("zen", "(248) YouTube")) is None

    def test_youtube_is_left_to_the_model_because_the_title_decides(self):
        assert self._one(sample("Google Chrome", "Integration by parts", "https://youtube.com/watch?v=1")) is None

    def test_dual_purpose_apps_are_left_to_the_model(self):
        for app in ("Discord", "Notion", "Spotify", "Safari"):
            assert self._one(sample(app, "something")) is None, app


class TestVerdictCache:
    def test_a_confident_verdict_is_reused(self):
        cache = VerdictCache()
        cache.put("k", {"verdict": "off_task", "confidence": 90, "decided_by": "text_model"})
        assert cache.get("k")["verdict"] == "off_task"
        assert cache.get("k")["decided_by"] == "cache"

    def test_an_unconfident_verdict_is_not_reused(self):
        cache = VerdictCache()
        cache.put("k", {"verdict": "off_task", "confidence": 40, "decided_by": "text_model"})
        assert cache.get("k") is None

    def test_ambiguity_is_never_cached_so_it_can_escalate_later(self):
        cache = VerdictCache()
        cache.put("k", {"verdict": "ambiguous", "confidence": 95, "decided_by": "text_model"})
        assert cache.get("k") is None

    def test_a_screenshot_verdict_overrides_the_text_one(self):
        cache = VerdictCache()
        cache.put("k", {"verdict": "off_task", "confidence": 80, "decided_by": "text_model"})
        cache.put("k", {"verdict": "on_task", "confidence": 60, "decided_by": "vision_model"})
        assert cache.get("k")["verdict"] == "on_task"
