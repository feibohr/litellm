from typing import Dict, Optional, Any, Union, List
import os
import random
import asyncio
import httpx
from litellm.types.router import LiteLLM_Params
from litellm._logging import verbose_proxy_logger

class ProxyConfig:
    """
    Handles proxy configuration with the following priority levels:
    1. Model level (highest priority) - litellm_params.proxy_config
    2. Provider level (lowest priority) - LiteLLM_Config.provider_proxy_config from database
    
    Now supports multiple proxy configurations per provider with automatic failover.
    """
    
    def __init__(self):
        self.provider_proxy_config: Dict[str, Any] = {}
        # Cache for failed proxies to avoid immediate retry
        self._failed_proxies_cache: Dict[str, List[str]] = {}
        
    async def get_proxy_config_dynamic(
        self, 
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None,
        prisma_client=None
    ) -> Dict[str, Optional[str]]:
        """
        Get proxy configuration dynamically from database on each request
        This ensures configuration changes take effect immediately without restart
        
        Args:
            litellm_params: Optional LiteLLM parameters containing model-level proxy config
            custom_llm_provider: Provider name for provider-specific proxy config
            prisma_client: Database client for dynamic loading
            
        Returns:
            Dict containing proxy configuration
        """
        proxy_config = {}
        
        # 1. Check model-level proxy config (highest priority)
        if litellm_params and hasattr(litellm_params, 'proxy_config') and litellm_params.proxy_config:
            verbose_proxy_logger.debug(f"Using model-level proxy config: {litellm_params.proxy_config}")
            proxy_config.update(litellm_params.proxy_config)
            
        # 2. Dynamically load provider-level proxy config from database
        provider_config = await self._get_provider_proxy_config_dynamic(custom_llm_provider, prisma_client)
        if provider_config:
            verbose_proxy_logger.debug(f"Using provider-level proxy config for {custom_llm_provider}: {provider_config}")
            for key, value in provider_config.items():
                if key not in proxy_config:  # Only use if not set by model-level config
                    proxy_config[key] = value
                    
        # Normalize proxy configuration for httpx compatibility
        normalized_config = self._normalize_proxy_config(proxy_config)
        
        verbose_proxy_logger.debug(f"Final proxy configuration: {normalized_config}")
        return normalized_config

    async def get_multi_proxy_config_dynamic(
        self,
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None,
        prisma_client=None
    ) -> Optional[Dict[str, Any]]:
        """
        Get multi-proxy configuration dynamically from database
        
        Returns:
            Dict containing multi-proxy configuration or None if not configured
        """
        if not custom_llm_provider:
            return None
            
        provider_config = await self._get_provider_proxy_config_dynamic(custom_llm_provider, prisma_client)
        if not provider_config:
            return None
            
        # Check if this is a multi-proxy configuration
        if "proxies" in provider_config and isinstance(provider_config["proxies"], list):
            return provider_config
            
        return None

    async def _get_provider_proxy_config_dynamic(
        self, 
        custom_llm_provider: Optional[str],
        prisma_client=None
    ) -> Dict[str, Any]:
        """
        Dynamically get provider-specific proxy configuration from database
        Falls back to in-memory configuration if database is not available
        
        Args:
            custom_llm_provider: The provider name to look up
            prisma_client: Database client
            
        Returns:
            Dict containing provider-specific proxy configuration
        """
        if not custom_llm_provider:
            return {}
            
        try:
            # Get database client
            if prisma_client is None:
                try:
                    from litellm.proxy.proxy_server import prisma_client as global_prisma_client
                    prisma_client = global_prisma_client
                except ImportError:
                    verbose_proxy_logger.debug("No prisma client available for dynamic config load")
                    prisma_client = None
            
            # Try to load from database first
            if prisma_client is not None:
                # Query database for fresh configuration
                config_record = await prisma_client.db.litellm_config.find_unique(
                    where={"param_name": "provider_proxy_config"}
                )
                
                if config_record and config_record.param_value:
                    # Parse configuration
                    import json
                    if isinstance(config_record.param_value, str):
                        provider_proxy_config = json.loads(config_record.param_value)
                    else:
                        provider_proxy_config = config_record.param_value
                    
                    verbose_proxy_logger.debug(f"🔄 Dynamically loaded proxy config from DB: {provider_proxy_config}")
                    
                    # Look for exact provider match
                    if custom_llm_provider in provider_proxy_config:
                        return provider_proxy_config[custom_llm_provider]
                        
                    # Look for wildcard/pattern matches
                    for provider_pattern, config in provider_proxy_config.items():
                        if self._provider_matches_pattern(custom_llm_provider, provider_pattern):
                            return config
                            
                    return {}
                else:
                    verbose_proxy_logger.debug(f"No provider proxy config found in database for {custom_llm_provider}")
            else:
                verbose_proxy_logger.debug("No database connection available for dynamic config load")
            
            # Fallback to in-memory configuration
            verbose_proxy_logger.debug(f"🔄 Falling back to in-memory proxy config for {custom_llm_provider}")
            
            if not self.provider_proxy_config:
                verbose_proxy_logger.debug("No in-memory proxy configuration available")
                return {}
                
            # Look for exact provider match in memory
            if custom_llm_provider in self.provider_proxy_config:
                verbose_proxy_logger.debug(f"✅ Found in-memory config for {custom_llm_provider}: {self.provider_proxy_config[custom_llm_provider]}")
                return self.provider_proxy_config[custom_llm_provider]
                
            # Look for wildcard/pattern matches in memory
            for provider_pattern, config in self.provider_proxy_config.items():
                if self._provider_matches_pattern(custom_llm_provider, provider_pattern):
                    verbose_proxy_logger.debug(f"✅ Found in-memory pattern match for {custom_llm_provider}: {config}")
                    return config
                    
            verbose_proxy_logger.debug(f"No proxy configuration found for {custom_llm_provider}")
            return {}
            
        except Exception as e:
            verbose_proxy_logger.error(f"Error dynamically loading provider proxy config: {e}")
            
            # Final fallback to in-memory configuration
            verbose_proxy_logger.debug(f"🔄 Exception fallback to in-memory proxy config for {custom_llm_provider}")
            
            if self.provider_proxy_config and custom_llm_provider in self.provider_proxy_config:
                return self.provider_proxy_config[custom_llm_provider]
                
            return {}

    async def get_httpx_proxy_config_dynamic(
        self, 
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None,
        prisma_client=None
    ) -> Union[Dict[str, str], None]:
        """
        Dynamically get proxy configuration formatted for httpx client
        
        Args:
            litellm_params: LiteLLM parameters 
            custom_llm_provider: Provider name
            prisma_client: Database client
            
        Returns:
            Dict formatted for httpx.Client(proxies=...) or None if no proxy
        """
        verbose_proxy_logger.debug(f"🔧 Dynamically getting httpx proxy config for {custom_llm_provider}")
        
        # First check if this is a multi-proxy configuration
        multi_proxy_config = await self.get_multi_proxy_config_dynamic(litellm_params, custom_llm_provider, prisma_client)
        if multi_proxy_config:
            verbose_proxy_logger.info(f"🔄 Multi-proxy detected for {custom_llm_provider}, selecting next available proxy")
            # For multi-proxy, get the next available proxy (but use dynamic config)
            proxy_config = await self.get_next_available_proxy_dynamic(custom_llm_provider, None, prisma_client)
        else:
            verbose_proxy_logger.debug(f"📝 Single proxy mode for {custom_llm_provider}")
            # Use regular single proxy configuration (dynamic)
            proxy_config = await self.get_proxy_config_dynamic(litellm_params, custom_llm_provider, prisma_client)
        
        if not proxy_config:
            verbose_proxy_logger.debug(f"❌ No proxy configuration available for {custom_llm_provider}")
            return None
            
        httpx_config = {}
        
        # Convert to httpx format
        if proxy_config.get('http'):
            httpx_config['http://'] = proxy_config['http']
        if proxy_config.get('https'):
            httpx_config['https://'] = proxy_config['https']
        
        if httpx_config:
            verbose_proxy_logger.info(f"✅ Generated httpx proxy config for {custom_llm_provider}: {httpx_config}")
        else:
            verbose_proxy_logger.warning(f"⚠️  Empty httpx proxy config for {custom_llm_provider}")
            
        return httpx_config if httpx_config else None

    async def get_next_available_proxy_dynamic(
        self,
        custom_llm_provider: Optional[str] = None,
        failed_proxy_url: Optional[str] = None,
        prisma_client=None
    ) -> Optional[Dict[str, str]]:
        """
        Dynamically get the next available proxy for a provider, excluding failed ones
        
        Args:
            custom_llm_provider: Provider name
            failed_proxy_url: URL of the proxy that failed (to exclude from selection)
            prisma_client: Database client
            
        Returns:
            Dict containing proxy configuration or None if no proxy available
        """
        verbose_proxy_logger.debug(f"🔍 Dynamically getting next available proxy for {custom_llm_provider}")
        
        multi_proxy_config = await self.get_multi_proxy_config_dynamic(
            custom_llm_provider=custom_llm_provider, 
            prisma_client=prisma_client
        )
        if not multi_proxy_config:
            verbose_proxy_logger.debug(f"📝 No multi-proxy configuration for {custom_llm_provider}")
            return None
            
        proxies = multi_proxy_config.get("proxies", [])
        if not proxies:
            verbose_proxy_logger.warning(f"⚠️  Empty proxy list for {custom_llm_provider}")
            return None
        
        verbose_proxy_logger.debug(f"📋 Total proxies available for {custom_llm_provider}: {len(proxies)}")
            
        # Get failed proxies for this provider (still use cache for performance)
        failed_proxies = self._failed_proxies_cache.get(custom_llm_provider, [])
        
        # Add current failed proxy to failed list
        if failed_proxy_url:
            if failed_proxy_url not in failed_proxies:
                failed_proxies.append(failed_proxy_url)
                self._failed_proxies_cache[custom_llm_provider] = failed_proxies
                verbose_proxy_logger.warning(f"🚫 Added {failed_proxy_url} to failed proxy list for {custom_llm_provider}")
        
        verbose_proxy_logger.debug(f"❌ Failed proxies for {custom_llm_provider}: {failed_proxies}")
        
        # Find available proxies (not in failed list)
        available_proxies = []
        for proxy in proxies:
            proxy_url = proxy.get("http") or proxy.get("https")
            if proxy_url and proxy_url not in failed_proxies:
                available_proxies.append(proxy)
        
        verbose_proxy_logger.info(f"✅ Available proxies for {custom_llm_provider}: {len(available_proxies)}/{len(proxies)}")
        
        # If no available proxies, clear failed cache and try again
        if not available_proxies:
            verbose_proxy_logger.warning(
                f"🔄 All proxies failed for provider {custom_llm_provider}, resetting failed cache"
            )
            verbose_proxy_logger.info(f"   Previously failed proxies: {failed_proxies}")
            self._failed_proxies_cache[custom_llm_provider] = []
            available_proxies = proxies
            verbose_proxy_logger.info(f"   Reset: now {len(available_proxies)} proxies available")
        
        if available_proxies:
            # Return a random available proxy to distribute load
            selected_proxy = random.choice(available_proxies)
            normalized_proxy = self._normalize_proxy_config(selected_proxy)
            
            proxy_url = normalized_proxy.get('http') or normalized_proxy.get('https', 'Unknown')
            verbose_proxy_logger.info(
                f"🎯 Selected proxy for {custom_llm_provider}: {proxy_url} "
                f"(from {len(available_proxies)} available)"
            )
            verbose_proxy_logger.debug(f"   Full proxy config: {normalized_proxy}")
            
            return normalized_proxy
            
        verbose_proxy_logger.error(f"💀 No proxies available for {custom_llm_provider}")
        return None

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
                   Or multi-proxy format: {provider_name: {proxies: [...], retry_count: 3, retry_delay: 1}}
        """
        self.provider_proxy_config = config
        
        # Log configuration details
        verbose_proxy_logger.info("🔧 Setting provider proxy configuration:")
        for provider, provider_config in config.items():
            if isinstance(provider_config, dict) and "proxies" in provider_config:
                # Multi-proxy configuration
                proxy_count = len(provider_config.get("proxies", []))
                retry_count = provider_config.get("retry_count", 3)
                retry_delay = provider_config.get("retry_delay", 1.0)
                verbose_proxy_logger.info(
                    f"  📋 {provider}: Multi-proxy mode - {proxy_count} proxies, "
                    f"retry_count={retry_count}, retry_delay={retry_delay}s"
                )
                for i, proxy in enumerate(provider_config.get("proxies", []), 1):
                    proxy_url = proxy.get('http') or proxy.get('https', 'N/A')
                    verbose_proxy_logger.info(f"    🔗 Proxy {i}: {proxy_url}")
            else:
                # Single proxy configuration
                proxy_url = provider_config.get('http') or provider_config.get('https', 'N/A')
                verbose_proxy_logger.info(f"  📋 {provider}: Single proxy - {proxy_url}")
        
        verbose_proxy_logger.debug(f"Full proxy config: {config}")
        
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
            
    def get_config_hash(self) -> str:
        """
        Generate a hash of the current provider proxy configuration
        Used for change detection
        
        Returns:
            str: Hash of the current configuration
        """
        import hashlib
        import json
        
        config_str = json.dumps(self.provider_proxy_config or {}, sort_keys=True)
        return hashlib.md5(config_str.encode()).hexdigest()
    
    async def check_and_reload_config_if_changed(self, prisma_client=None) -> bool:
        """
        Check if database configuration has changed and reload if necessary
        
        Args:
            prisma_client: Optional Prisma client instance
            
        Returns:
            bool: True if configuration was reloaded, False otherwise
        """
        try:
            # Use provided client or import global one
            if prisma_client is None:
                try:
                    from litellm.proxy.proxy_server import prisma_client as global_prisma_client
                    prisma_client = global_prisma_client
                except ImportError:
                    return False
            
            if prisma_client is None:
                return False
            
            # Get current configuration from database
            config_record = await prisma_client.db.litellm_config.find_unique(
                where={"param_name": "provider_proxy_config"}
            )
            
            if not config_record:
                # No config in database
                if self.provider_proxy_config:
                    # We had config before, but now it's gone - reload
                    verbose_proxy_logger.info("🔄 Provider proxy config removed from database, reloading...")
                    return await self.reload_provider_proxy_config(prisma_client)
                return False
            
            # Parse database config
            import json
            if isinstance(config_record.param_value, str):
                db_config = json.loads(config_record.param_value)
            else:
                db_config = config_record.param_value
            
            # Compare with current config
            current_config = self.provider_proxy_config or {}
            
            if db_config != current_config:
                verbose_proxy_logger.info("🔄 Database configuration changed, reloading...")
                return await self.reload_provider_proxy_config(prisma_client)
            
            return False
            
        except Exception as e:
            verbose_proxy_logger.error(f"❌ Error checking config changes: {e}")
            return False

    def get_multi_proxy_config(
        self,
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Non-dynamic version of get_multi_proxy_config for backward compatibility
        
        Returns:
            Dict containing multi-proxy configuration or None if not configured
        """
        if not custom_llm_provider:
            return None
            
        # Use in-memory configuration
        if not self.provider_proxy_config:
            return None
            
        # Look for exact provider match
        if custom_llm_provider in self.provider_proxy_config:
            provider_config = self.provider_proxy_config[custom_llm_provider]
            # Check if this is a multi-proxy configuration
            if isinstance(provider_config, dict) and "proxies" in provider_config and isinstance(provider_config["proxies"], list):
                return provider_config
                
        # Look for wildcard/pattern matches
        for provider_pattern, config in self.provider_proxy_config.items():
            if self._provider_matches_pattern(custom_llm_provider, provider_pattern):
                if isinstance(config, dict) and "proxies" in config and isinstance(config["proxies"], list):
                    return config
                    
        return None

    def get_httpx_proxy_config(
        self, 
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None
    ) -> Union[Dict[str, str], None]:
        """
        Non-dynamic version of get_httpx_proxy_config for backward compatibility
        
        Args:
            litellm_params: LiteLLM parameters 
            custom_llm_provider: Provider name
            
        Returns:
            Dict formatted for httpx.Client(proxies=...) or None if no proxy
        """
        verbose_proxy_logger.debug(f"🔧 Getting httpx proxy config for {custom_llm_provider}")
        
        # First check if this is a multi-proxy configuration
        multi_proxy_config = self.get_multi_proxy_config(litellm_params, custom_llm_provider)
        if multi_proxy_config:
            verbose_proxy_logger.info(f"🔄 Multi-proxy detected for {custom_llm_provider}, selecting next available proxy")
            # For multi-proxy, get the next available proxy
            proxy_config = self.get_next_available_proxy(custom_llm_provider)
        else:
            verbose_proxy_logger.debug(f"📝 Single proxy mode for {custom_llm_provider}")
            # Use regular single proxy configuration
            proxy_config = self.get_proxy_config(litellm_params, custom_llm_provider)
        
        if not proxy_config:
            verbose_proxy_logger.debug(f"❌ No proxy configuration available for {custom_llm_provider}")
            return None
            
        httpx_config = {}
        
        # Convert to httpx format
        if proxy_config.get('http'):
            httpx_config['http://'] = proxy_config['http']
        if proxy_config.get('https'):
            httpx_config['https://'] = proxy_config['https']
        
        if httpx_config:
            verbose_proxy_logger.info(f"✅ Generated httpx proxy config for {custom_llm_provider}: {httpx_config}")
        else:
            verbose_proxy_logger.warning(f"⚠️  Empty httpx proxy config for {custom_llm_provider}")
            
        return httpx_config if httpx_config else None

    def get_proxy_config(
        self, 
        litellm_params: Optional[LiteLLM_Params] = None,
        custom_llm_provider: Optional[str] = None
    ) -> Dict[str, Optional[str]]:
        """
        Non-dynamic version of get_proxy_config for backward compatibility
        
        Args:
            litellm_params: Optional LiteLLM parameters containing model-level proxy config
            custom_llm_provider: Provider name for provider-specific proxy config
            
        Returns:
            Dict containing proxy configuration
        """
        proxy_config = {}
        
        # 1. Check model-level proxy config (highest priority)
        if litellm_params and hasattr(litellm_params, 'proxy_config') and litellm_params.proxy_config:
            verbose_proxy_logger.debug(f"Using model-level proxy config: {litellm_params.proxy_config}")
            proxy_config.update(litellm_params.proxy_config)
            
        # 2. Use provider-level proxy config from memory
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

    def get_next_available_proxy(
        self,
        custom_llm_provider: Optional[str] = None,
        failed_proxy_url: Optional[str] = None
    ) -> Optional[Dict[str, str]]:
        """
        Non-dynamic version of get_next_available_proxy for backward compatibility
        
        Args:
            custom_llm_provider: Provider name
            failed_proxy_url: URL of the proxy that failed (to exclude from selection)
            
        Returns:
            Dict containing proxy configuration or None if no proxy available
        """
        verbose_proxy_logger.debug(f"🔍 Getting next available proxy for {custom_llm_provider}")
        
        multi_proxy_config = self.get_multi_proxy_config(custom_llm_provider=custom_llm_provider)
        if not multi_proxy_config:
            verbose_proxy_logger.debug(f"📝 No multi-proxy configuration for {custom_llm_provider}")
            return None
            
        proxies = multi_proxy_config.get("proxies", [])
        if not proxies:
            verbose_proxy_logger.warning(f"⚠️  Empty proxy list for {custom_llm_provider}")
            return None
        
        verbose_proxy_logger.debug(f"📋 Total proxies available for {custom_llm_provider}: {len(proxies)}")
            
        # Get failed proxies for this provider
        failed_proxies = self._failed_proxies_cache.get(custom_llm_provider, [])
        
        # Add the current failed proxy to the cache if provided
        if failed_proxy_url and failed_proxy_url not in failed_proxies:
            failed_proxies.append(failed_proxy_url)
            self._failed_proxies_cache[custom_llm_provider] = failed_proxies
            verbose_proxy_logger.info(f"💥 Added failed proxy to cache for {custom_llm_provider}: {failed_proxy_url}")
        
        # Filter out failed proxies
        available_proxies = []
        for proxy in proxies:
            proxy_url = proxy.get('http') or proxy.get('https')
            if proxy_url not in failed_proxies:
                available_proxies.append(proxy)
        
        verbose_proxy_logger.debug(f"🔍 Available proxies for {custom_llm_provider}: {len(available_proxies)}")
        
        if not available_proxies:
            verbose_proxy_logger.warning(f"💀 No available proxies for {custom_llm_provider} (all failed)")
            # Clear the failed proxy cache and retry
            self._failed_proxies_cache[custom_llm_provider] = []
            verbose_proxy_logger.info(f"🔄 Cleared failed proxy cache for {custom_llm_provider}, retrying...")
            available_proxies = proxies
        
        # Randomly select an available proxy
        if available_proxies:
            import random
            selected_proxy = random.choice(available_proxies)
            proxy_url = selected_proxy.get('http') or selected_proxy.get('https', 'N/A')
            verbose_proxy_logger.info(f"🎯 Selected proxy for {custom_llm_provider}: {proxy_url}")
            return selected_proxy
        
        verbose_proxy_logger.error(f"💀 No proxy available for {custom_llm_provider}")
        return None

    def _get_provider_proxy_config(self, custom_llm_provider: Optional[str]) -> Dict[str, Any]:
        """
        Get provider-specific proxy configuration from memory
        
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

# Global proxy config instance
global_proxy_config = ProxyConfig() 