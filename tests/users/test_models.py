import pytest

from apps.users.models import User


@pytest.mark.django_db
def test_user_str_returns_email():
    user = User.objects.create_user(
        username="userstr",
        email="userstr@test.com",
        password="pass12345",
    )

    assert str(user) == "userstr@test.com"
