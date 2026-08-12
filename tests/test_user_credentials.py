from app.services.user_credentials import phone_to_login_email, resolve_user_credentials


def test_phone_to_login_email():
    assert phone_to_login_email("9876543210") == "9876543210@gmail.com"
    assert phone_to_login_email("+91 98765 43210") == "919876543210@gmail.com"


def test_resolve_user_credentials_defaults_password_to_phone():
    email, password = resolve_user_credentials(phone="9876543210")
    assert email == "9876543210@gmail.com"
    assert password == "9876543210"


def test_resolve_user_credentials_custom_password():
    email, password = resolve_user_credentials(phone="9876543210", password="custom-pass-1")
    assert email == "9876543210@gmail.com"
    assert password == "custom-pass-1"
