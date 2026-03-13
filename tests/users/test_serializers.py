from unittest.mock import patch

import pytest
from rest_framework import serializers

from apps.users.models import User
from apps.users.serializers import LoginSerializer


@pytest.mark.django_db
@patch("apps.users.serializers.authenticate")
def test_login_serializer_rejects_inactive_user(mock_authenticate):
    user = User.objects.create_user(
        username="inactive-serializer",
        email="inactive-serializer@test.com",
        password="pass12345",
    )
    user.is_active = False

    mock_authenticate.return_value = user

    serializer = LoginSerializer(data={
        "email": "inactive-serializer@test.com",
        "password": "pass12345",
    })

    with pytest.raises(serializers.ValidationError, match="Account is disabled."):
        serializer.is_valid(raise_exception=True)
