import pytest
from _pytest.outcomes import Failed

from tests.conftest import require_test_database


def test_accepts_a_test_database():
    require_test_database("postgresql://ebb:ebb@localhost:5432/ebb_test")


@pytest.mark.parametrize(
    "url",
    ["postgresql://ebb:ebb@localhost:5432/ebb", "postgresql://ebb:ebb@localhost:5432/"],
)
def test_refuses_any_other_database(url):
    with pytest.raises(Failed, match="_test"):
        require_test_database(url)
