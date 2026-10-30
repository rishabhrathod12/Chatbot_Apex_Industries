import app


def test_package_importable() -> None:
    assert app.__name__ == "app"
