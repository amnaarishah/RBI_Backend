"""Login, tokens and role-based access (viewer < engineer < admin) on a TEMPORARY database.

Passwords are random per run; nothing here is a real credential.
Run from backend/:  python -m unittest tests.test_auth -v
"""
import os
import secrets
import sys
import unittest
import uuid
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import jwt  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.api import deps  # noqa: E402
from app.api.deps import required_role  # noqa: E402
from app.api.endpoints import auth as auth_endpoints  # noqa: E402
from app.core import security  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
import app.models.equipment, app.models.inspection, app.models.cml, app.models.user  # noqa: E402,F401
from app.main import app  # noqa: E402
from app.models.user import User  # noqa: E402

BASE_URL = make_url(settings.DATABASE_URL)
TEST_DB = "ams_test_" + uuid.uuid4().hex[:8]
engine = create_engine(BASE_URL.set(database=TEST_DB), pool_pre_ping=True)
Session = sessionmaker(autocommit=False, autoflush=False, bind=engine)
PW = {r: secrets.token_urlsafe(14) for r in ("admin", "engineer", "viewer")}


_auth_before = settings.AUTH_REQUIRED


def setUpModule():
    settings.AUTH_REQUIRED = True            # login is off by default; these tests check it switched ON
    admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'CREATE DATABASE "{TEST_DB}"'))
    admin.dispose()
    Base.metadata.create_all(bind=engine)
    db = Session()
    for role in PW:
        db.add(User(username=f"{role}1", full_name=role.title(), role=role,
                    password_hash=security.hash_password(PW[role])))
    db.commit()
    db.close()

    def override():
        db = Session()
        try:
            yield db
        finally:
            db.close()
    app.dependency_overrides[get_db] = override


def tearDownModule():
    settings.AUTH_REQUIRED = _auth_before
    app.dependency_overrides.clear()
    engine.dispose()
    admin = create_engine(BASE_URL.set(database="postgres"), isolation_level="AUTOCOMMIT")
    with admin.connect() as c:
        c.execute(text(f'DROP DATABASE IF EXISTS "{TEST_DB}" WITH (FORCE)'))
    admin.dispose()


class PasswordsAndTokens(unittest.TestCase):
    def test_hash_and_verify(self):
        h = security.hash_password("a-long-enough-pass")
        self.assertTrue(h.startswith("scrypt$"))
        self.assertTrue(security.verify_password("a-long-enough-pass", h))
        self.assertFalse(security.verify_password("a-long-enough-pasS", h))
        self.assertNotEqual(h, security.hash_password("a-long-enough-pass"))      # random salt
        self.assertFalse(security.verify_password("x", "garbage"))

    def test_password_rules(self):
        self.assertIsNotNone(security.validate_new_password("short"))
        self.assertIsNotNone(security.validate_new_password(" leading-space-here"))
        self.assertIsNone(security.validate_new_password("long-enough-1"))

    def test_token_roundtrip_expiry_and_tampering(self):
        t = security.create_access_token("bob", "viewer")
        self.assertEqual(security.decode_access_token(t)["sub"], "bob")
        self.assertIsNone(security.decode_access_token(security.create_access_token("bob", "viewer", minutes=-1)))
        self.assertIsNone(security.decode_access_token(t[:-3] + "abc"))
        forged = jwt.encode({"sub": "bob", "role": "admin",
                             "exp": datetime.now(timezone.utc) + timedelta(hours=1)}, "another-key", algorithm="HS256")
        self.assertIsNone(security.decode_access_token(forged))
        none_alg = jwt.encode({"sub": "bob"}, None, algorithm="none")
        self.assertIsNone(security.decode_access_token(none_alg))

    def test_role_rule_per_method(self):
        self.assertEqual(required_role("GET", "/api/v1/equipment"), "viewer")
        self.assertEqual(required_role("POST", "/api/v1/equipment"), "engineer")
        self.assertEqual(required_role("PUT", "/api/v1/inspection/abc"), "engineer")
        self.assertEqual(required_role("DELETE", "/api/v1/inspection/abc"), "admin")
        self.assertEqual(required_role("POST", "/api/v1/asme/calculate-tmin"), "viewer")
        self.assertEqual(required_role("POST", "/api/v1/auth/change-password"), "viewer")
        self.assertEqual(required_role("GET", "/api/v1/auth/users"), "admin")
        self.assertEqual(required_role("PATCH", "/api/v1/auth/users/bob"), "admin")


class Api(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def setUp(self):
        auth_endpoints._failures.clear()

    def login(self, role):
        r = self.client.post("/api/v1/auth/login", json={"username": f"{role}1", "password": PW[role]})
        self.assertEqual(r.status_code, 200, r.text)
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    NEW_EQ = {"equipment_id": "T-1", "equipment_name": "Test", "equipment_type": "Vessel",
              "created_by": "someone-else"}

    def test_public_and_protected(self):
        self.assertEqual(self.client.get("/health").status_code, 200)
        self.assertEqual(self.client.get("/api/v1/equipment").status_code, 401)
        self.assertEqual(self.client.get("/api/v1/equipment", headers={"Authorization": "Bearer nonsense"}).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/equipment", json=self.NEW_EQ).status_code, 401)

    def test_login_failures_and_throttle(self):
        bad = {"username": "viewer1", "password": "wrong-password"}
        self.assertEqual(self.client.post("/api/v1/auth/login", json=bad).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"username": "nobody", "password": "x"}).status_code, 401)
        for _ in range(auth_endpoints.MAX_FAILURES):
            self.client.post("/api/v1/auth/login", json=bad)
        self.assertEqual(self.client.post("/api/v1/auth/login", json=bad).status_code, 429)
        # even the right password is refused while locked
        self.assertEqual(self.client.post("/api/v1/auth/login",
                                          json={"username": "viewer1", "password": PW["viewer"]}).status_code, 429)

    def test_viewer_reads_but_cannot_write(self):
        h = self.login("viewer")
        self.assertEqual(self.client.get("/api/v1/equipment", headers=h).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/equipment", json=self.NEW_EQ, headers=h).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/auth/users", headers=h).status_code, 403)
        calc = {"component_type": "cylindrical_shell", "design_pressure": 5, "pressure_unit": "bar",
                "inside_diameter": 1000, "dimension_unit": "mm", "design_temperature": 150,
                "temperature_unit": "celsius", "joint_efficiency": 0.85, "spec_number": "X", "grade": "X",
                "product_form": "X", "custom_stress": 137.9, "custom_stress_unit": "mpa",
                "custom_stress_source": "test"}
        self.assertEqual(self.client.post("/api/v1/asme/calculate-tmin", json=calc, headers=h).status_code, 200)

    def test_engineer_writes_but_cannot_delete_and_user_is_recorded(self):
        h = self.login("engineer")
        r = self.client.post("/api/v1/equipment", json=self.NEW_EQ, headers=h)
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["created_by"], "engineer1")                    # not "someone-else"
        eid = r.json()["id"]
        r = self.client.put(f"/api/v1/equipment/{eid}", json={"equipment_name": "Renamed", "equipment_type": "Vessel"}, headers=h)
        self.assertEqual((r.status_code, r.json()["updated_by"]), (200, "engineer1"))
        self.assertEqual(self.client.delete(f"/api/v1/equipment/{eid}", headers=h).status_code, 403)
        ha = self.login("admin")
        self.assertEqual(self.client.delete(f"/api/v1/equipment/{eid}", headers=ha).status_code, 200)

    def test_upload_requires_engineer(self):
        pdf = {"file": ("doc.pdf", b"%PDF-1.4 test", "application/pdf")}
        self.assertEqual(self.client.post("/api/v1/equipment/upload-document", files=pdf,
                                          headers=self.login("viewer")).status_code, 403)
        r = self.client.post("/api/v1/equipment/upload-document", files=pdf, headers=self.login("engineer"))
        self.assertEqual(r.status_code, 200, r.text)
        fake = {"file": ("doc.pdf", b"MZ not a pdf", "application/pdf")}
        self.assertEqual(self.client.post("/api/v1/equipment/upload-document", files=fake,
                                          headers=self.login("engineer")).status_code, 400)
        path = os.path.join(os.path.dirname(__file__), "..", r.json()["document_url"].lstrip("/"))
        if os.path.exists(path):
            os.remove(path)

    def test_user_management_and_last_admin_protection(self):
        ha = self.login("admin")
        newpw = secrets.token_urlsafe(14)
        r = self.client.post("/api/v1/auth/users", headers=ha,
                             json={"username": "New.User", "role": "engineer", "password": newpw})
        self.assertEqual((r.status_code, r.json()["username"]), (201, "new.user"))
        self.assertEqual(self.client.post("/api/v1/auth/users", headers=ha,
                                          json={"username": "new.user", "role": "viewer", "password": newpw}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/users", headers=ha,
                                          json={"username": "weak1", "role": "viewer", "password": "short"}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/users", headers=ha,
                                          json={"username": "odd1", "role": "king", "password": newpw}).status_code, 400)
        # the only admin cannot be demoted or deactivated
        self.assertEqual(self.client.patch("/api/v1/auth/users/admin1", headers=ha, json={"role": "viewer"}).status_code, 400)
        self.assertEqual(self.client.patch("/api/v1/auth/users/admin1", headers=ha, json={"is_active": False}).status_code, 400)
        # a deactivated user's existing token stops working
        hn = {"Authorization": "Bearer " + self.client.post("/api/v1/auth/login", json={"username": "new.user", "password": newpw}).json()["access_token"]}
        self.assertEqual(self.client.get("/api/v1/equipment", headers=hn).status_code, 200)
        self.assertEqual(self.client.patch("/api/v1/auth/users/new.user", headers=ha, json={"is_active": False}).status_code, 200)
        self.assertEqual(self.client.get("/api/v1/equipment", headers=hn).status_code, 401)

    def test_change_password(self):
        h = self.login("viewer")
        new = secrets.token_urlsafe(14)
        self.assertEqual(self.client.post("/api/v1/auth/change-password", headers=h,
                                          json={"current_password": "wrong", "new_password": new}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/change-password", headers=h,
                                          json={"current_password": PW["viewer"], "new_password": "short"}).status_code, 400)
        self.assertEqual(self.client.post("/api/v1/auth/change-password", headers=h,
                                          json={"current_password": PW["viewer"], "new_password": new}).status_code, 200)
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"username": "viewer1", "password": PW["viewer"]}).status_code, 401)
        self.assertEqual(self.client.post("/api/v1/auth/login", json={"username": "viewer1", "password": new}).status_code, 200)
        PW["viewer"] = new

    def test_me(self):
        r = self.client.get("/api/v1/auth/me", headers=self.login("engineer"))
        self.assertEqual((r.status_code, r.json()["role"]), (200, "engineer"))
        self.assertNotIn("password", r.text)

    def test_auth_can_be_switched_off_for_local_experiments(self):
        settings.AUTH_REQUIRED = False
        try:
            self.assertEqual(self.client.get("/api/v1/equipment").status_code, 200)
        finally:
            settings.AUTH_REQUIRED = True
        self.assertEqual(self.client.get("/api/v1/equipment").status_code, 401)


if __name__ == "__main__":
    unittest.main()
