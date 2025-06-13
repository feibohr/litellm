from typing import Dict, Optional, Any, Union
import os
from litellm.types.router import LiteLLM_Params
from litellm._logging import verbose_proxy_logger

class ProxyConfig:
    """
    Handles proxy configuration with the following priority levels:
    1. Model level (highest priority) - litellm_params.proxy_config
    2. Provider level (lowest priority) - LiteLLM_Config.provider_proxy_config from database
    """
    
    def __init__(self):
        self.provider_proxy_config: Dict[str, Any] = {}
        
    def get_proxy_config(
        self, 
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        """
        Get proxy configuration based on priority levels
        
        Args:
            litellm_params: Optional LiteLLM parameters containing model-level proxy config
            custom_llm_provider: Provider name for provider-specific proxy config
            
        Returns:
            Dict containing proxy configuration with keys:
            - http: HTTP proxy URL
            - https: HTTPS proxy URL  
            - no_proxy: Comma-separated list of hosts to bypass proxy
        """
        proxy_config = {}
        
        # 1. Check model-level proxy config (highest priority)
        if litellm_params and hasattr(litellm_params, 'proxy_config') and litellm_params.proxy_config:
            verbose_proxy_logger.debug(f"Using model-level proxy config: {litellm_params.proxy_config}")
            proxy_config.update(litellm_params.proxy_config)
            
        # 2. Check provider-level proxy config from database (lowest priority)
        provider_config = self._get_provider_proxy_config(custom_llm_provider)
        if provider_config:
            verbose_proxy_logger.debug(f"Using provider-level proxy config for {custom_llm_provider}: {provider_config}")
            for key, value in provider_config.items():
                if key not in proxy_config:  # Only use if not set by model-level config
                    proxy_config[key] = value
                    
        # Normalize proxy configuration for httpx compatibility
        normalized_config = self._normalize_proxy_config(proxy_config)
        
        verbose_proxy_logger.debug(f"Final proxy configuration: {normalized_config}")
        return normalized_config
        
    def _get_provider_proxy_config(self, custom_llm_provider: Optional[str]) -> Dict[str, Any]:
        """
        Get provider-specific proxy configuration from database LiteLLM_Config.provider_proxy_config
        
        Args:
            custom_llm_provider: The provider name to look up
            
        Returns:
            Dict containing provider-specific proxy configuration
        """
        if not custom_llm_provider or not self.provider_proxy_config:
            return {}
            
        # Look for exact provider match
        if custom_llm_provider in self.provider_proxy_config:
            return self.provider_proxy_config[custom_llm_provider]
            
        # Look for wildcard/pattern matches
        for provider_pattern, config in self.provider_proxy_config.items():
            if self._provider_matches_pattern(custom_llm_provider, provider_pattern):
                return config
                
        return {}
        
    def _provider_matches_pattern(self, provider: str, pattern: str) -> bool:
        """
        Check if provider matches a pattern (supports wildcards)
        
        Args:
            provider: The provider name
            pattern: The pattern to match against
            
        Returns:
            bool: True if provider matches pattern
        """
        if pattern == "*":
            return True
        if pattern.endswith("*"):
            return provider.startswith(pattern[:-1])
        if pattern.startswith("*"):
            return provider.endswith(pattern[1:])
        return provider == pattern
        
    def _normalize_proxy_config(self, proxy_config: Dict[str, Any]) -> Dict[str, Optional[str]]:
        """
        Normalize proxy configuration for httpx compatibility
        
        Args:
            proxy_config: Raw proxy configuration
            
        Returns:
            Normalized proxy configuration
        """
        normalized = {}
        
        # Handle common proxy URL formats
        for key, value in proxy_config.items():
            if key in ['http', 'https']:
                normalized[key] = self._normalize_proxy_url(value)
            elif key == 'no_proxy':
                normalized['no_proxy'] = value
            elif key == 'all':
                # Handle 'all' proxy that applies to both http and https
                normalized['http'] = self._normalize_proxy_url(value)
                normalized['https'] = self._normalize_proxy_url(value)
                
        return normalized
        
    def _normalize_proxy_url(self, proxy_url: str) -> str:
        """
        Normalize proxy URL to ensure proper format
        
        Args:
            proxy_url: Raw proxy URL
            
        Returns:
            Normalized proxy URL
        """
        if not proxy_url:
            return proxy_url
            
        # Add scheme if missing
        if not proxy_url.startswith(('http://', 'https://', 'socks5://', 'socks4://')):
            proxy_url = f"http://{proxy_url}"
            
        return proxy_url
        
    def set_provider_proxy_config(self, config: Dict[str, Any]) -> None:
        """
        Set provider-level proxy configuration
        
        Args:
            config: Dictionary containing provider proxy configuration
                   Format: {provider_name: {http: url, https: url, no_proxy: hosts}}
        """
        self.provider_proxy_config = config
        verbose_proxy_logger.debug(f"Set provider proxy config: {config}")
        
    async def load_provider_proxy_config_from_db(self, prisma_client) -> None:
        """
        Load provider proxy configuration from database (LiteLLM_Config table)
        
        Args:
            prisma_client: Prisma client instance for database access
        """
        try:
            if prisma_client is None:
                verbose_proxy_logger.debug("No prisma client provided, skipping proxy config load")
                return
                
            # Query LiteLLM_Config table for provider_proxy_config
            config_record = await prisma_client.db.litellm_config.find_unique(
                where={"param_name": "provider_proxy_config"}
            )
            
            if config_record and config_record.param_value:
                import json
                if isinstance(config_record.param_value, str):
                    self.provider_proxy_config = json.loads(config_record.param_value)
                else:
                    self.provider_proxy_config = config_record.param_value
                verbose_proxy_logger.debug(f"Loaded provider proxy config from DB: {self.provider_proxy_config}")
            else:
                verbose_proxy_logger.debug("No provider proxy config found in database")
                
        except Exception as e:
            verbose_proxy_logger.error(f"Error loading provider proxy config from DB: {e}")
            
    async def update_provider_proxy_config_in_db(self, prisma_client, config: Dict[str, Any]) -> None:
        """
        Update provider proxy configuration in database
        
        Args:
            prisma_client: Prisma client instance
            config: Provider proxy configuration to save
        """
        try:
            if prisma_client is None:
                verbose_proxy_logger.error("No prisma client provided, cannot update proxy config")
                return
                
            import json
            config_json = json.dumps(config)
            
            # Upsert configuration in LiteLLM_Config table
            await prisma_client.db.litellm_config.upsert(
                where={"param_name": "provider_proxy_config"},
                data={
                    "create": {
                        "param_name": "provider_proxy_config",
                        "param_value": config_json
                    },
                    "update": {
                        "param_value": config_json
                    }
                }
            )
            
            # Update local config
            self.provider_proxy_config = config
            verbose_proxy_logger.info(f"Updated provider proxy config in DB: {config}")
            
        except Exception as e:
            verbose_proxy_logger.error(f"Error updating provider proxy config in DB: {e}")
            raise
            
    def get_httpx_proxy_config(
        self, 
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None
    ) -> Union[Dict[str, str], None]:
        """
        Get proxy configuration formatted for httpx client
        
        Args:
            litellm_params: LiteLLM parameters 
            custom_llm_provider: Provider name
            
        Returns:
            Dict formatted for httpx.Client(proxies=...) or None if no proxy
        """
        proxy_config = self.get_proxy_config(litellm_params, custom_llm_provider)
        
        if not proxy_config:
            return None
            
        httpx_config = {}
        
        # Convert to httpx format
        if proxy_config.get('http'):
            httpx_config['http://'] = proxy_config['http']
        if proxy_config.get('https'):
            httpx_config['https://'] = proxy_config['https']
            
        return httpx_config if httpx_config else None


# Global proxy config instance
global_proxy_config = ProxyConfig() 