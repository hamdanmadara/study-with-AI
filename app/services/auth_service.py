from fastapi import HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional, Dict, Any
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
from loguru import logger

from app.core.config import settings
from app.services.supabase_service import supabase_service


# Security setup
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthService:
    def __init__(self):
        self.secret_key = settings.jwt_secret_key
        self.algorithm = settings.jwt_algorithm
        
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against its hash"""
        return pwd_context.verify(plain_password, hashed_password)
    
    def get_password_hash(self, password: str) -> str:
        """Hash a password"""
        return pwd_context.hash(password)
    
    def create_access_token(self, data: Dict[str, Any], expires_delta: Optional[timedelta] = None) -> str:
        """Create JWT access token"""
        to_encode = data.copy()
        
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(hours=settings.jwt_expiration_hours)
            
        to_encode.update({"exp": expire})
        
        encoded_jwt = jwt.encode(to_encode, self.secret_key, algorithm=self.algorithm)
        return encoded_jwt
    
    def verify_token(self, token: str) -> Optional[Dict[str, Any]]:
        """Verify JWT token and return payload"""
        try:
            payload = jwt.decode(token, self.secret_key, algorithms=[self.algorithm])
            return payload
        except JWTError as e:
            logger.error(f"JWT verification failed: {e}")
            return None
    
    async def authenticate_user(self, email: str, password: str) -> Optional[Dict[str, Any]]:
        """Authenticate user with email and password"""
        try:
            # Use Supabase authentication
            response = supabase_service.supabase.auth.sign_in_with_password({
                "email": email,
                "password": password
            })
            
            if response.user and response.session:
                return {
                    "user_id": response.user.id,
                    "email": response.user.email,
                    "access_token": response.session.access_token,
                    "refresh_token": response.session.refresh_token,
                    "user_metadata": response.user.user_metadata or {}
                }
        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            
        return None
    
    async def register_user(self, email: str, password: str, metadata: Optional[Dict] = None) -> Dict[str, Any]:
        """Register a new user"""
        try:
            # Use Supabase authentication
            response = supabase_service.supabase.auth.sign_up({
                "email": email,
                "password": password,
                "options": {
                    "data": metadata or {}
                }
            })
            
            if response.user:
                return {
                    "success": True,
                    "user_id": response.user.id,
                    "email": response.user.email,
                    "message": "User registered successfully. Please check your email for verification." if not response.session else "User registered and logged in successfully.",
                    "session": response.session is not None
                }
            else:
                return {
                    "success": False,
                    "error": "Registration failed"
                }
        except Exception as e:
            logger.error(f"Registration failed: {e}")
            return {
                "success": False,
                "error": str(e)
            }
    
    async def refresh_token(self, refresh_token: str) -> Optional[Dict[str, Any]]:
        """Refresh access token"""
        try:
            response = supabase_service.supabase.auth.refresh_session(refresh_token)
            
            if response.session:
                return {
                    "access_token": response.session.access_token,
                    "refresh_token": response.session.refresh_token,
                    "user_id": response.user.id if response.user else None
                }
        except Exception as e:
            logger.error(f"Token refresh failed: {e}")
            
        return None
    
    async def logout_user(self, access_token: str) -> bool:
        """Logout user and invalidate token"""
        try:
            # Set the token and sign out
            supabase_service.supabase.auth.set_session(access_token, "")
            supabase_service.supabase.auth.sign_out()
            return True
        except Exception as e:
            logger.error(f"Logout failed: {e}")
            return False


# Global auth service instance
auth_service = AuthService()


# Dependency to get current user from JWT token
async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    Dependency to extract and validate current user from JWT token
    Returns user info if valid, raises HTTPException if invalid
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    
    try:
        token = credentials.credentials
        
        # First try Supabase JWT verification
        user_info = supabase_service.verify_jwt_token(token)
        
        if user_info:
            return user_info
        
        # Fallback to our own JWT verification for backward compatibility
        payload = auth_service.verify_token(token)
        if payload and "user_id" in payload:
            return {
                "user_id": payload["user_id"],
                "email": payload.get("email", ""),
                "user_metadata": payload.get("user_metadata", {})
            }
        
        raise credentials_exception
        
    except Exception as e:
        logger.error(f"Token validation error: {e}")
        raise credentials_exception


# Optional dependency for endpoints that can work with or without authentication
async def get_current_user_optional(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)) -> Optional[Dict[str, Any]]:
    """
    Optional dependency to get current user
    Returns user info if valid token provided, None if no token or invalid
    """
    if not credentials:
        return None
    
    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None


# User model for type hints
class CurrentUser:
    def __init__(self, user_data: Dict[str, Any]):
        self.user_id = user_data["user_id"]
        self.email = user_data.get("email", "")
        self.user_metadata = user_data.get("user_metadata", {})
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_id": self.user_id,
            "email": self.email,
            "user_metadata": self.user_metadata
        }