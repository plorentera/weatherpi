"""WeatherPi setuptools configuration."""

from pathlib import Path

from setuptools import find_packages, setup

BASE_DIR = Path(__file__).resolve().parent
README_FILE = BASE_DIR / "README.md"

setup(
    name="weatherpi",
    version="0.1.0",
    description="Open source local weather station with Python + FastAPI + SQLite",
    long_description=README_FILE.read_text(encoding="utf-8") if README_FILE.exists() else "",
    long_description_content_type="text/markdown",
    author="WeatherPi Contributors",
    license="GPL-3.0-or-later",
    url="https://github.com/yourusername/weatherpi",
    packages=find_packages(exclude=["tests", "docs", "scripts"]),
    python_requires=">=3.11",
    install_requires=[
        "fastapi==0.135.3",
        "uvicorn[standard]==0.43.0",
        "pydantic>=2.12.5",
        "httpx>=0.28.1",
        "paho-mqtt>=2.1.0",
        "python-dotenv>=1.2.2",
    ],
    extras_require={
        "dev": [
            "pytest>=7.4.0",
            "pytest-cov>=4.1.0",
            "pytest-asyncio>=0.21.0",
            "black>=23.0.0",
            "isort>=5.12.0",
            "pylint>=2.17.0",
            "mypy>=1.0.0",
            "ruff>=0.1.0",
            "pre-commit>=3.0.0",
        ]
    },
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Environment :: Web Environment",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
        "Programming Language :: Python :: 3.14",
        "Topic :: Scientific/Engineering :: Atmospheric Science",
    ],
)
