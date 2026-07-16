from app import __version__


def test_application_version_is_0_2_0() -> None:
    assert __version__ == "0.2.0"
