from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from app.config import get_settings
from app.providers.factory import agent_model_specs

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    username: str


class SignupRequest(BaseModel):
    username: str
    password: str


class InfoResponse(BaseModel):
    username: str
    model: str
    storage: str
    version: str = "0.5.0"


@router.get("/info", response_model=InfoResponse)
async def get_info() -> InfoResponse:
    """Public endpoint — returns display info needed before login."""
    settings = get_settings()
    specs = agent_model_specs(settings)
    model = specs.get("orchestrator") or specs.get("conversational") or "unknown"
    storage = "supabase+pgvector" if settings.database_url else "sqlite+chromadb"
    username = settings.sage_username or "sage"
    return InfoResponse(username=username, model=model, storage=storage)


@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, request: Request) -> LoginResponse:
    """Validate username + password against the registry."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Server not ready")

    user = registry.verify_password(body.username.strip(), body.password)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    return LoginResponse(ok=True, username=user["username"])


@router.post("/signup", response_model=LoginResponse, status_code=status.HTTP_201_CREATED)
async def signup(body: SignupRequest, request: Request) -> LoginResponse:
    """Create a new account and return the user."""
    registry = getattr(request.app.state, "registry", None)
    if registry is None:
        raise HTTPException(status_code=503, detail="Server not ready")

    username = body.username.strip()
    password = body.password

    if len(username) < 3:
        raise HTTPException(status_code=422, detail="Username must be at least 3 characters")
    if len(password) < 6:
        raise HTTPException(status_code=422, detail="Password must be at least 6 characters")

    if registry.get_user_by_username(username):
        raise HTTPException(status_code=409, detail="Username already taken")

    user = registry.create_user(username, password)
    return LoginResponse(ok=True, username=user["username"])
