"""Конфигурация тестов и общие фикстуры."""

from pathlib import Path

import pytest
from PIL import Image


@pytest.fixture(scope="session", autouse=True)
def create_test_fixtures() -> None:
    """Создает необходимые директории и тестовые файлы (изображения) для прогона тестов."""
    fixtures_dir = Path("tests/fixtures")
    fixtures_dir.mkdir(parents=True, exist_ok=True)

    passport_path = fixtures_dir / "passport.jpg"
    if not passport_path.exists():
        # Генерируем простое изображение заглушку для тестов
        img = Image.new("RGB", (100, 100), color="blue")
        img.save(passport_path)
