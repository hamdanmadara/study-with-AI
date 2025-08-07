from fastapi import APIRouter, HTTPException, Depends, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr
from typing import Optional, Dict, Any

from app.services.auth_service import auth_service, get_current_user, CurrentUser


router = APIRouter(prefix="/auth", tags=["authentication"])


# Request models
class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    confirm_password: str
    full_name: Optional[str] = None


class RefreshTokenRequest(BaseModel):
    refresh_token: str


# Response models
class AuthResponse(BaseModel):
    success: bool
    message: str
    user_id: Optional[str] = None
    email: Optional[str] = None
    access_token: Optional[str] = None
    refresh_token: Optional[str] = None
    user_metadata: Optional[Dict[str, Any]] = None


@router.post("/register", response_model=AuthResponse)
async def register(request: RegisterRequest):
    """Register a new user account"""
    try:
        # Validate passwords match
        if request.password != request.confirm_password:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Passwords do not match"
            )
        
        # Validate password strength
        if len(request.password) < 6:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Password must be at least 6 characters long"
            )
        
        # Prepare user metadata
        metadata = {}
        if request.full_name:
            metadata["full_name"] = request.full_name
        
        # Register user
        result = await auth_service.register_user(
            email=request.email,
            password=request.password,
            metadata=metadata
        )
        
        if result["success"]:
            return AuthResponse(
                success=True,
                message=result["message"],
                user_id=result["user_id"],
                email=result["email"]
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=result["error"]
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Registration failed: {str(e)}"
        )


@router.post("/login", response_model=AuthResponse)
async def login(request: LoginRequest):
    """Login with email and password"""
    try:
        # Authenticate user
        auth_result = await auth_service.authenticate_user(
            email=request.email,
            password=request.password
        )
        
        if auth_result:
            return AuthResponse(
                success=True,
                message="Login successful",
                user_id=auth_result["user_id"],
                email=auth_result["email"],
                access_token=auth_result["access_token"],
                refresh_token=auth_result["refresh_token"],
                user_metadata=auth_result["user_metadata"]
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid email or password"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Login failed: {str(e)}"
        )


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(request: RefreshTokenRequest):
    """Refresh access token using refresh token"""
    try:
        result = await auth_service.refresh_token(request.refresh_token)
        
        if result:
            return AuthResponse(
                success=True,
                message="Token refreshed successfully",
                user_id=result.get("user_id"),
                access_token=result["access_token"],
                refresh_token=result["refresh_token"]
            )
        else:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid refresh token"
            )
    
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Token refresh failed: {str(e)}"
        )


@router.post("/logout")
async def logout(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Logout current user"""
    try:
        # Note: In a real implementation, you might want to blacklist the token
        # For now, we'll just return success as the frontend will discard the token
        
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": True,
                "message": "Logout successful"
            }
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Logout failed: {str(e)}"
        )


@router.get("/me")
async def get_current_user_info(current_user: Dict[str, Any] = Depends(get_current_user)):
    """Get current user information"""
    try:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "success": True,
                "user": {
                    "user_id": current_user["user_id"],
                    "email": current_user["email"],
                    "user_metadata": current_user.get("user_metadata", {})
                }
            }
        )
    
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get user info: {str(e)}"
        )


@router.get("/health")
async def auth_health_check():
    """Check authentication service health"""
    try:
        return JSONResponse(
            status_code=status.HTTP_200_OK,
            content={
                "service": "authentication",
                "status": "healthy",
                "timestamp": "2024-01-01T00:00:00Z"  # This would be actual timestamp
            }
        )
    
    except Exception as e:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "service": "authentication",
                "status": "unhealthy",
                "error": str(e)
            }
        )