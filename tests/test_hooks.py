import pytest

from flagd_ugc.errors import QualityError
from flagd_ugc.hooks import validate_hook, validate_title, word_count


def test_hook_accepts_first_person_under_twelve_words():
    hook = "I finally found the easier way to show my plans"
    assert word_count(hook) == 10
    assert validate_hook(hook) == hook


def test_hook_rejects_twelve_words_because_limit_is_strictly_under_twelve():
    hook = "I found a much easier way to share all my plans today"
    assert word_count(hook) == 12
    with pytest.raises(QualityError, match="under 12"):
        validate_hook(hook)


@pytest.mark.parametrize(
    "hook",
    [
        "This is a clearer way to share plans",
        "I guarantee this always works",
        "My method is 100% effective",
    ],
)
def test_hook_rejects_missing_perspective_or_unsupported_claims(hook):
    with pytest.raises(QualityError):
        validate_hook(hook)


def test_title_accepts_a_short_lower_case_label_grounded_in_its_demo():
    assert (
        validate_title(
            "the 43 conversation score",
            demo_description="The Progress screen shows an overall conversation score of 43 out of 100.",
        )
        == "the 43 conversation score"
    )


@pytest.mark.parametrize(
    "title",
    (
        "great",  # too short
        "The practice date feedback",  # capital letter
        "the practice date feedback 🔥",  # emoji
        "follow for practice date feedback",  # CTA
        "the 43 conversation score",  # unsupported number below
    ),
)
def test_title_rejects_non_upload_safe_copy(title):
    with pytest.raises(QualityError):
        validate_title(title, demo_description="The app gives feedback after a practice date.")
