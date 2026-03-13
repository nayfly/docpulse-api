import pytest
from rest_framework import status
from rest_framework.test import APIClient
from apps.users.models import User


@pytest.fixture
def api_client():
    return APIClient()


@pytest.fixture
def user(db):
    return User.objects.create_user(
        username="testuser",
        email="test@example.com",
        password="testpass123",
    )


@pytest.mark.django_db
class TestRegister:
    def test_register_returns_tokens(self, api_client):
        response = api_client.post("/api/auth/register/", {
            "email": "new@example.com",
            "username": "newuser",
            "password": "securepass123",
        }, format="json")
        assert response.status_code == status.HTTP_201_CREATED
        assert "tokens" in response.data
        assert "access" in response.data["tokens"]
        assert "refresh" in response.data["tokens"]

    def test_register_duplicate_email_fails(self, api_client, user):
        response = api_client.post("/api/auth/register/", {
            "email": "test@example.com",
            "username": "another",
            "password": "securepass123",
        }, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_register_short_password_fails(self, api_client):
        response = api_client.post("/api/auth/register/", {
            "email": "short@example.com",
            "username": "shortpass",
            "password": "abc",
        }, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestLogin:
    def test_login_valid_credentials(self, api_client, user):
        response = api_client.post("/api/auth/login/", {
            "email": "test@example.com",
            "password": "testpass123",
        }, format="json")
        assert response.status_code == status.HTTP_200_OK
        assert "tokens" in response.data

    def test_login_wrong_password(self, api_client, user):
        response = api_client.post("/api/auth/login/", {
            "email": "test@example.com",
            "password": "wrongpass",
        }, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST

    def test_login_nonexistent_user(self, api_client):
        response = api_client.post("/api/auth/login/", {
            "email": "ghost@example.com",
            "password": "somepass",
        }, format="json")
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestLogout:
    def test_logout_blacklists_token(self, api_client, user):
        from rest_framework_simplejwt.tokens import RefreshToken
        refresh = RefreshToken.for_user(user)
        api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {str(refresh.access_token)}")

        response = api_client.post("/api/auth/logout/", {
            "refresh": str(refresh),
        }, format="json")
        assert response.status_code == status.HTTP_200_OK

    def test_logout_requires_auth(self, api_client):
        response = api_client.post("/api/auth/logout/", {"refresh": "token"})
        assert response.status_code == status.HTTP_401_UNAUTHORIZED