from fastapi import APIRouter, Depends

from app.auth import AuthenticatedPrincipal, require_authenticated_user


router = APIRouter(prefix="/api/auth", tags=["Authentication"])


@router.get("/me")
def get_authenticated_identity(
    principal: AuthenticatedPrincipal = Depends(require_authenticated_user),
):
    return {
        "authenticated": True,
        "identity": {
            "issuer": principal.issuer,
            "subject": principal.subject,
            "username": principal.username,
        },
        "roles": list(principal.roles),
        "departments": list(principal.departments),
    }
