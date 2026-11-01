import json
from pathlib import Path


REALM_EXPORT = (
    Path(__file__).resolve().parents[2]
    / "keycloak"
    / "realm-export"
    / "CIT-Nexus-realm.json"
)


def test_registration_requires_account_type_and_does_not_assign_admin():
    realm = json.loads(REALM_EXPORT.read_text(encoding="utf-8"))
    assert realm["registrationAllowed"] is True

    account_type = next(
        attribute
        for attribute in realm["userProfile"]["attributes"]
        if attribute["name"] == "account_type"
    )
    assert account_type["required"] == {"roles": ["user"]}
    assert account_type["annotations"]["inputType"] == "select"
    assert account_type["validations"]["options"]["options"] == [
        "student",
        "admin_request",
    ]

    assert "ADMIN" not in realm.get("defaultRoles", [])
