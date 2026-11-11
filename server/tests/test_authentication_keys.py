import unittest
from types import SimpleNamespace

from fastapi import HTTPException
from fastapi.testclient import TestClient

from api.server import (
    ROLE_SECRETS,
    ROLE_SECRET_KEYS,
    UserRole,
    app,
    get_current_user_role,
    require_role,
)
from config.privilege_keys import (
    ADMIN_PRIVILEGE_KEYS,
    SYSTEM_PRIVILEGE_KEYS,
    USER_PRIVILEGE_KEYS,
)


class AuthenticationKeyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_all_configured_privilege_keys_authenticate_to_expected_role(self):
        configured_keys = {
            UserRole.USER: USER_PRIVILEGE_KEYS,
            UserRole.SYSTEM: SYSTEM_PRIVILEGE_KEYS,
            UserRole.ADMIN: ADMIN_PRIVILEGE_KEYS,
        }
        for role, keys in configured_keys.items():
            with self.subTest(role=role.value):
                self.assertGreaterEqual(len(keys), 3)
                self.assertEqual(ROLE_SECRET_KEYS[role], keys)
                for key in keys:
                    self.assertEqual(get_current_user_role(key), role)

    def test_all_privileged_keys_can_read_system_endpoint(self):
        # The viewer credential is now part of the configured SYSTEM tuple.
        # Exercise every key through the HTTP authentication dependency.
        for role in (UserRole.SYSTEM, UserRole.ADMIN):
            for index, key in enumerate(ROLE_SECRET_KEYS[role]):
                with self.subTest(role=role.value, key_index=index):
                    response = self.client.get("/environments", headers={"X-API-Key": key})
                    self.assertEqual(response.status_code, 200)

    def test_basic_roles_cannot_read_system_endpoint(self):
        self.assertEqual(self.client.get("/environments").status_code, 403)
        for role in (UserRole.USER, UserRole.AGENT):
            for index, key in enumerate(ROLE_SECRET_KEYS[role]):
                with self.subTest(role=role.value, key_index=index):
                    response = self.client.get("/environments", headers={"X-API-Key": key})
                    self.assertEqual(response.status_code, 403)

    def test_role_secrets_keep_first_key_compatibility(self):
        for role, keys in ROLE_SECRET_KEYS.items():
            with self.subTest(role=role.value):
                self.assertEqual(ROLE_SECRETS[role], keys[0])

    def test_existing_agent_authentication_and_omitted_key_behavior_remain(self):
        self.assertEqual(get_current_user_role(None), UserRole.AGENT)
        self.assertEqual(
            get_current_user_role(ROLE_SECRETS[UserRole.AGENT]),
            UserRole.AGENT,
        )

        self.assertEqual(self.client.get("/version").status_code, 200)
        agent_headers = {"X-API-Key": ROLE_SECRETS[UserRole.AGENT]}
        self.assertEqual(self.client.get("/version", headers=agent_headers).status_code, 200)

    def test_invalid_key_is_rejected(self):
        with self.assertRaises(HTTPException) as context:
            get_current_user_role("invalid-api-key")

        self.assertEqual(context.exception.status_code, 401)

        response = self.client.get("/version", headers={"X-API-Key": "invalid-api-key"})
        self.assertEqual(response.status_code, 401)

    def test_role_hierarchy_still_inherits_permissions(self):
        request = SimpleNamespace(state=SimpleNamespace())

        self.assertEqual(
            require_role(UserRole.USER)(request, current_role=UserRole.USER),
            UserRole.USER,
        )
        self.assertEqual(
            require_role(UserRole.USER)(request, current_role=UserRole.SYSTEM),
            UserRole.SYSTEM,
        )
        self.assertEqual(
            require_role(UserRole.SYSTEM)(request, current_role=UserRole.ADMIN),
            UserRole.ADMIN,
        )

        with self.assertRaises(HTTPException) as context:
            require_role(UserRole.SYSTEM)(request, current_role=UserRole.USER)

        self.assertEqual(context.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
