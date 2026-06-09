from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def propfind_listing() -> bytes:
    return (FIXTURES / "propfind_listing.xml").read_bytes()


@pytest.fixture
def propfind_apache() -> bytes:
    return (FIXTURES / "propfind_apache.xml").read_bytes()
