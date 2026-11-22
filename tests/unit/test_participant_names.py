import pytest

from app.services.participant_names import normalize_summary_names, participant_name


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("Павел Шечков", "Павел"),
        ("Планы Павла Щечкова", "Планы Павла"),
        ("Позвони Павлу Шечкову", "Позвони Павлу"),
        ("С Павлом Щечковым", "С Павлом"),
        ("О Павле Шечкове", "О Павле"),
        ("Pavel Shechkov", "Павел"),
        ("Shechkov Pavel", "Павел"),
        ("Павел Щечковский", "Павел Щечковский"),
        ("Алексей Шечков", "Алексей Шечков"),
        ("Шечков", "Шечков"),
    ],
)
def test_known_aliases_preserve_case_and_other_people(source, expected):
    assert normalize_summary_names(source) == expected


def test_display_name_uses_id_not_profile_name():
    assert participant_name(205580587, "Любое имя") == "Павел"
    assert participant_name(42, "Павел Другой") == "Павел Другой"
    assert participant_name(None, "Имя") == "Имя"
