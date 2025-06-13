"""
API endpoints for managing proxy configurations in LiteLLM proxy
"""

from typing import Dict, Any
from fastapi import HTTPException, Depends, Request
from litellm.proxy._types import UserAPIKeyAuth
from litellm.proxy.auth.user_api_key_auth import user_api_key_auth
from litellm.proxy.utils import update_provider_proxy_config
from litellm.proxy.proxy_config import global_proxy_config


async def update_provider_proxy_config_endpoint(
    request: Request,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
    prisma_client=None
):
    """
    Update provider-level proxy configuration in database
    
    Request body should contain provider proxy configuration:
    {
        "openai": {
            "http": "http://openai-proxy.company.com:8080",
            "https": "http://openai-proxy.company.com:8080"
        },
        "azure*": {
            "http": "http://azure-proxy.company.com:8080",
            "https": "http://azure-proxy.company.com:8080"
        }
    }
    """
    try:
        # Only admin users can update proxy configuration
        if user_api_key_dict.user_role != "proxy_admin":
            raise HTTPException(
                status_code=403,
                detail={"error": "Only admin users can update proxy configuration"}
            )
        
        # Parse request body
        request_data = await request.json()
        
        # Validate request data
        if not isinstance(request_data, dict):
            raise HTTPException(
                status_code=400,
                detail={"error": "Request body must be a dictionary"}
            )
        
        # Validate proxy configuration format
        for provider, config in request_data.items():
            if not isinstance(config, dict):
                raise HTTPException(
                    status_code=400,
                    detail={"error": f"Configuration for provider '{provider}' must be a dictionary"}
                )
            
            # Check for valid proxy configuration keys
            valid_keys = {'http', 'https', 'no_proxy', 'all'}
            for key in config.keys():
                if key not in valid_keys:
                    raise HTTPException(
                        status_code=400,
                        detail={"error": f"Invalid proxy configuration key '{key}' for provider '{provider}'. Valid keys: {valid_keys}"}
                    )
        
        # Update provider proxy configuration
        await update_provider_proxy_config(request_data, prisma_client)
        
        return {
            "status": "success",
            "message": "Provider proxy configuration updated successfully",
            "updated_config": request_data
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": f"Failed to update proxy configuration: {str(e)}"}
        )


async def get_provider_proxy_config_endpoint(
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    Get current provider-level proxy configuration from database
    """
    try:
        # Only admin users can view proxy configuration
        if user_api_key_dict.user_role != "proxy_admin":
            raise HTTPException(
                status_code=403,
                detail={"error": "Only admin users can view proxy configuration"}
            )
        
        return {
            "status": "success",
            "provider_proxy_config": global_proxy_config.provider_proxy_config
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": f"Failed to get proxy configuration: {str(e)}"}
        )


async def delete_provider_proxy_config_endpoint(
    provider_name: str,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
    prisma_client=None
):
    """
    Delete a specific provider's proxy configuration
    """
    try:
        # Only admin users can delete proxy configuration
        if user_api_key_dict.user_role != "proxy_admin":
            raise HTTPException(
                status_code=403,
                detail={"error": "Only admin users can delete proxy configuration"}
            )
        
        # Get current configuration
        current_config = global_proxy_config.provider_proxy_config.copy()
        
        # Check if provider exists
        if provider_name not in current_config:
            raise HTTPException(
                status_code=404,
                detail={"error": f"Provider '{provider_name}' not found in proxy configuration"}
            )
        
        # Remove provider configuration
        del current_config[provider_name]
        
        # Update configuration in database
        await update_provider_proxy_config(current_config, prisma_client)
        
        return {
            "status": "success",
            "message": f"Proxy configuration for provider '{provider_name}' deleted successfully"
        }
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": f"Failed to delete proxy configuration: {str(e)}"}
        )


async def test_proxy_config_endpoint(
    test_provider: str,
    user_api_key_dict: UserAPIKeyAuth = Depends(user_api_key_auth),
):
    """
    Test proxy configuration for a specific provider
    """
    try:
        # Only admin users can test proxy configuration
        if user_api_key_dict.user_role != "proxy_admin":
            raise HTTPException(
                status_code=403,
                detail={"error": "Only admin users can test proxy configuration"}
            )
        
        # Get proxy configuration for the provider
        proxy_config = global_proxy_config.get_proxy_config(
            custom_llm_provider=test_provider
        )
        
        # Get httpx-compatible configuration
        httpx_config = global_proxy_config.get_httpx_proxy_config(
            custom_llm_provider=test_provider
        )
        
        return {
            "status": "success",
            "provider": test_provider,
            "resolved_proxy_config": proxy_config,
            "httpx_proxy_config": httpx_config,
            "test_result": "Proxy configuration resolved successfully"
        }
        
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"error": f"Failed to test proxy configuration: {str(e)}"}
        ) 