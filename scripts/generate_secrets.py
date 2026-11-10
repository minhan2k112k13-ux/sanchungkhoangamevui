"""Generate secrets for the SÀN CHỨNG KHOÁN Render environment."""
import secrets
import string

# Ensure the generated Owner password meets the application's letter+digit rule.
alphabet = string.ascii_letters + string.digits + "-_!@#$%"
while True:
    owner_password = "".join(secrets.choice(alphabet) for _ in range(32))
    if any(c.isalpha() for c in owner_password) and any(c.isdigit() for c in owner_password):
        break

print("SESSION_SECRET=" + secrets.token_urlsafe(48))
print("OWNER_PASSWORD=" + owner_password)
