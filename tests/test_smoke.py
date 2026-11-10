import uuid

from fastapi.testclient import TestClient

import main


def json_post(client, csrf, path, body):
    return client.post(path, json=body, headers={"X-CSRF-Token": csrf})


def test_new_account_autologin_market_trade_and_relogin(tmp_path, monkeypatch):
    app = main.create_app(f"sqlite:///{tmp_path / 'market.db'}")
    with TestClient(app) as client:
        status = client.get("/api/auth/status").json()
        csrf = status["csrf"]
        registered = json_post(client, csrf, "/api/auth/register", {
            "username": "student01", "password": "Password123", "display_name": "Student One"
        })
        assert registered.status_code == 200, registered.text
        payload = registered.json()
        assert payload["user"]["balance"] == 500000.0
        assert payload["csrf"]
        assert client.get("/api/me").status_code == 200  # Registration auto-logs in.
        csrf = payload["csrf"]
        market = client.get("/api/market")
        assert market.status_code == 200
        assert len(market.json()["assets"]) == 10
        assert {x["symbol"] for x in market.json()["assets"]} == {
            "GOLD", "SILVER", "COPPER", "PLATINUM", "OIL", "BRENT", "GAS", "BTC", "ETH", "SOL"
        }
        bought = json_post(client, csrf, "/api/trade", {
            "symbol": "BTC", "mode": "buy", "amount_usd": "1000"
        })
        assert bought.status_code == 200, bought.text
        assert bought.json()["user"]["balance"] < 500000
        assert bought.json()["user"]["holdings"]["BTC"] > 0
        trades = client.get("/api/trades").json()["trades"]
        assert trades and trades[0]["type"] == "MUA"
        # Logout, refresh CSRF, and log in with the same account.
        assert json_post(client, csrf, "/api/auth/logout", {}).status_code == 200
        csrf = client.get("/api/auth/status").json()["csrf"]
        logged_in = json_post(client, csrf, "/api/auth/login", {
            "username": "STUDENT01", "password": "Password123"
        })
        assert logged_in.status_code == 200, logged_in.text
        assert logged_in.json()["user"]["username"] == "student01"


def test_owner_admin_membership_chat_and_permissions(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OWNER_PASSWORD", "OwnerPassword123")
    monkeypatch.setattr(main, "OWNER_DISPLAY_NAME", "Owner Test")
    app = main.create_app(f"sqlite:///{tmp_path / 'roles.db'}")
    with TestClient(app) as owner:
        owner_csrf = owner.get("/api/auth/status").json()["csrf"]
        assert owner.get("/healthz").json()["owner_configured"] is True
        owner_login = json_post(owner, owner_csrf, "/api/auth/login", {
            "username": "baominhadmin", "password": "OwnerPassword123"
        })
        assert owner_login.status_code == 200, owner_login.text
        owner_csrf = owner_login.json()["csrf"]
        assert owner_login.json()["user"]["role"] == "owner"
        assert owner.post("/api/auth/bootstrap-owner", json={}).status_code == 404

        with TestClient(app) as member:
            csrf = member.get("/api/auth/status").json()["csrf"]
            reg = json_post(member, csrf, "/api/auth/register", {
                "username": "member01", "password": "Password123", "display_name": "Member One"
            })
            assert reg.status_code == 200, reg.text
            csrf = reg.json()["csrf"]
            member_id = reg.json()["user"]["account_id"]
            assert member.get("/api/chat/qtv").status_code == 403
            assert json_post(member, csrf, "/api/chat/total", {"message": "Xin chào mọi người"}).status_code == 200
            assert member.get("/api/membership/shop").status_code == 200
            assert all(x["key"] != "ruby" for x in member.get("/api/membership/shop").json()["items"])
            request_admin = json_post(member, csrf, "/api/role/request-admin", {"note": "Xin xét duyệt"})
            assert request_admin.status_code == 200, request_admin.text

            reqs = owner.get("/api/admin/requests").json()["requests"]
            assert len(reqs) == 1
            approved = json_post(owner, owner_csrf, f"/api/admin/requests/{reqs[0]['id']}/decision", {"approve": True})
            assert approved.status_code == 200, approved.text

            # Owner can change virtual balance of another account.
            adjusted = json_post(owner, owner_csrf, "/api/admin/cash", {
                "target_username": "member01", "amount": "2500", "action": "add"
            })
            assert adjusted.status_code == 200, adjusted.text
            assert adjusted.json()["user"]["balance"] == 502500.0

            # Ruby is gift-only; an Owner gift is immediate and revocable only by Owner.
            ruby = json_post(owner, owner_csrf, "/api/membership/gift", {
                "recipient_id": member_id, "recipient_name": "Member One", "tier": "ruby"
            })
            assert ruby.status_code == 200, ruby.text
            assert owner.post("/api/membership/revoke", json={"username": "member01"},
                              headers={"X-CSRF-Token": owner_csrf}).status_code == 200

            # Owner may promote the account, and member cannot call Owner-only controls.
            promoted = json_post(owner, owner_csrf, "/api/admin/roles/change", {
                "username": "member01", "direction": "promote"
            })
            assert promoted.status_code == 200, promoted.text
            assert member.get("/api/admin/role-limits").status_code == 403

        # Owner-only settings / audit / backup are reachable for Owner.
        limits = owner.get("/api/admin/role-limits")
        assert limits.status_code == 200
        assert limits.json()["role_limits"] == {"superadmin": 5, "admin": 10, "coordinate": 20}
        assert owner.get("/api/admin/audit").status_code == 200
        assert owner.get("/api/admin/backup").status_code == 200
        assert owner.get("/healthz").json()["ok"] is True

    # Changing/removing the environment password after initial provisioning
    # must not silently replace the already-created Owner credential.
    monkeypatch.setattr(main, "OWNER_PASSWORD", "NewOwnerPassword456")
    with TestClient(app) as restarted:
        csrf = restarted.get("/api/auth/status").json()["csrf"]
        old_password_login = json_post(restarted, csrf, "/api/auth/login", {
            "username": "baominhadmin", "password": "OwnerPassword123"
        })
        assert old_password_login.status_code == 200, old_password_login.text


def test_auth_screen_only_has_register_and_login(tmp_path):
    app = main.create_app(f"sqlite:///{tmp_path / 'auth-ui.db'}")
    with TestClient(app) as client:
        page = client.get("/")
        assert page.status_code == 200
        html = page.text
        assert 'data-auth-tab="register"' in html
        assert 'data-auth-tab="login"' in html
        assert 'data-auth-tab="owner"' not in html
        assert 'id="owner-form"' not in html
        assert 'OWNER_SETUP_KEY' not in html
        assert "USD ảo" not in html and "USD ẢO" not in html
        assert "placeholder=" not in html


def test_owner_can_delete_non_owner_account(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OWNER_PASSWORD", "OwnerPassword123")
    monkeypatch.setattr(main, "OWNER_DISPLAY_NAME", "Owner")
    app = main.create_app(f"sqlite:///{tmp_path / 'delete.db'}")
    with TestClient(app) as owner:
        csrf = owner.get("/api/auth/status").json()["csrf"]
        data = json_post(owner, csrf, "/api/auth/login", {
            "username": "baominhadmin", "password": "OwnerPassword123"
        }).json()
        csrf = data["csrf"]
        with TestClient(app) as member:
            mcsrf = member.get("/api/auth/status").json()["csrf"]
            member_data = json_post(member, mcsrf, "/api/auth/register", {
                "username": "remove_me", "password": "Password123", "display_name": "Remove Me"
            }).json()
            assert member_data["user"]["username"] == "remove_me"
        deleted = json_post(owner, csrf, "/api/admin/users/delete", {"username": "remove_me"})
        assert deleted.status_code == 200, deleted.text
        assert owner.get("/api/admin/users").status_code == 200
        assert all(u["username"] != "remove_me" for u in owner.get("/api/admin/users").json()["users"])


def test_spadmin_ruby_gift_waits_for_owner_approval(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "OWNER_PASSWORD", "OwnerPassword123")
    monkeypatch.setattr(main, "OWNER_DISPLAY_NAME", "Owner")
    db_url = f"sqlite:///{tmp_path / 'ruby-approval.db'}"
    app = main.create_app(db_url)
    with TestClient(app) as owner:
        csrf_o = owner.get("/api/auth/status").json()["csrf"]
        owner_data = json_post(owner, csrf_o, "/api/auth/login", {
            "username": "baominhadmin", "password": "OwnerPassword123"
        }).json()
        csrf_o = owner_data["csrf"]

        with TestClient(app) as spadmin:
            csrf_s = spadmin.get("/api/auth/status").json()["csrf"]
            staff = json_post(spadmin, csrf_s, "/api/auth/register", {
                "username": "staff01", "password": "Password123", "display_name": "Staff One"
            }).json()
            csrf_s = staff["csrf"]
        with TestClient(app) as target:
            csrf_t = target.get("/api/auth/status").json()["csrf"]
            target_data = json_post(target, csrf_t, "/api/auth/register", {
                "username": "ruby_target", "password": "Password123", "display_name": "Ruby Target"
            }).json()
            target_id = target_data["user"]["account_id"]

        promoted = json_post(owner, csrf_o, "/api/admin/roles/change", {
            "username": "staff01", "direction": "grant_spadmin"
        })
        assert promoted.status_code == 200, promoted.text
        # Existing staff session sees the upgraded role because roles are loaded from DB each request.
        with TestClient(app) as spadmin:
            csrf_s = spadmin.get("/api/auth/status").json()["csrf"]
            login = json_post(spadmin, csrf_s, "/api/auth/login", {
                "username": "staff01", "password": "Password123"
            })
            assert login.status_code == 200
            csrf_s = login.json()["csrf"]
            request = json_post(spadmin, csrf_s, "/api/membership/gift", {
                "recipient_id": target_id, "recipient_name": "Ruby Target", "tier": "ruby"
            })
            assert request.status_code == 200, request.text
            assert request.json().get("pending") is True

        reports = owner.get("/api/reports").json()["reports"]
        ruby_request = next(x for x in reports if x["type"] == "ruby_gift_request")
        approved = json_post(owner, csrf_o, f"/api/reports/{ruby_request['id']}/decision", {
            "decision": "approve_ruby_gift", "reply": "Đã duyệt theo yêu cầu."
        })
        assert approved.status_code == 200, approved.text
        # The recipient logs in and receives the special membership.
        with TestClient(app) as target:
            csrf_t = target.get("/api/auth/status").json()["csrf"]
            login = json_post(target, csrf_t, "/api/auth/login", {
                "username": "ruby_target", "password": "Password123"
            })
            assert login.status_code == 200
            assert login.json()["user"]["membership"] == "ruby"

        # ADMIN cannot punish Ruby directly; the request is routed to SPADMIN.
        with TestClient(app) as admin_client:
            csrf_a = admin_client.get("/api/auth/status").json()["csrf"]
            admin_data = json_post(admin_client, csrf_a, "/api/auth/register", {
                "username": "admin01", "password": "Password123", "display_name": "Admin One"
            }).json()
        for _ in range(2):
            promoted = json_post(owner, csrf_o, "/api/admin/roles/change", {
                "username": "admin01", "direction": "promote"
            })
            assert promoted.status_code == 200, promoted.text
        with TestClient(app) as admin_client:
            csrf_a = admin_client.get("/api/auth/status").json()["csrf"]
            login_a = json_post(admin_client, csrf_a, "/api/auth/login", {
                "username": "admin01", "password": "Password123"
            })
            assert login_a.status_code == 200
            csrf_a = login_a.json()["csrf"]
            routed = json_post(admin_client, csrf_a, "/api/admin/users/penalty", {
                "username": "ruby_target", "amount": "1000", "reason": "Kiểm tra luồng phạt RUBY"
            })
            assert routed.status_code == 200, routed.text
            assert routed.json().get("pending") is True

        with TestClient(app) as spadmin:
            csrf_s = spadmin.get("/api/auth/status").json()["csrf"]
            login_s = json_post(spadmin, csrf_s, "/api/auth/login", {
                "username": "staff01", "password": "Password123"
            })
            assert login_s.status_code == 200
            csrf_s = login_s.json()["csrf"]
            pending = spadmin.get("/api/reports").json()["reports"]
            penalty_request = next(x for x in pending if x["type"] == "ruby_penalty_request")
            resolved = json_post(spadmin, csrf_s, f"/api/reports/{penalty_request['id']}/spadmin-decision", {
                "decision": "approve"
            })
            assert resolved.status_code == 200, resolved.text

        with TestClient(app) as target:
            csrf_t = target.get("/api/auth/status").json()["csrf"]
            login_t = json_post(target, csrf_t, "/api/auth/login", {
                "username": "ruby_target", "password": "Password123"
            })
            assert login_t.status_code == 200
            assert login_t.json()["user"]["ruby_immunity_left"] == 2


def test_web_files_and_database_survive_app_restart(tmp_path):
    db_url = f"sqlite:///{tmp_path / 'persist.db'}"
    app1 = main.create_app(db_url)
    with TestClient(app1) as client:
        assert "SÀN CHỨNG KHOÁN" in client.get("/").text
        assert client.get("/static/styles.css").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        csrf = client.get("/api/auth/status").json()["csrf"]
        reg = json_post(client, csrf, "/api/auth/register", {
            "username": "persist01", "password": "Password123", "display_name": "Keep Me"
        })
        assert reg.status_code == 200

    # A fresh app/engine against the same database sees the account and its hash.
    app2 = main.create_app(db_url)
    with TestClient(app2) as client:
        csrf = client.get("/api/auth/status").json()["csrf"]
        login = json_post(client, csrf, "/api/auth/login", {
            "username": "persist01", "password": "Password123"
        })
        assert login.status_code == 200, login.text
        assert client.get("/api/me").json()["user"]["balance"] == 500000.0
