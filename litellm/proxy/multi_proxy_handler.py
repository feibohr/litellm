"""
Multi-Proxy Handler for LiteLLM
Handles automatic proxy failover and retry logic
"""

import asyncio
import time
import random
from typing import Any, Callable, Dict, List, Optional, Union
import httpx
from litellm._logging import verbose_proxy_logger
from litellm.proxy.proxy_config import global_proxy_config


class ProxyConnectionError(Exception):
    """Exception raised when all proxy connections fail"""
    pass


class MultiProxyHandler:
    """
    Handles multi-proxy configurations with automatic failover
    """
    
    def __init__(self):
        # Cache for failed proxies with expiration
        self._failed_proxy_cache: Dict[str, Dict[str, float]] = {}
        self._cache_expiry_seconds = 300  # 5 minutes
    
    async def execute_with_proxy_retry(
        self,
        func: Callable,
        custom_llm_provider: str
    ) -> Any:
        """
        Execute request with proxy retry mechanism using dynamic configuration
        
        Args:
            func: Function that takes proxy_config and makes the request
            custom_llm_provider: Provider name (e.g., 'openrouter')
            
        Returns:
            Response from successful request
            
        Raises:
            Exception: If all proxies fail
        """
        verbose_proxy_logger.info(f"🔄 Starting multi-proxy retry for {custom_llm_provider}")
        
        # Get fresh configuration from database
        multi_proxy_config = await global_proxy_config.get_multi_proxy_config_dynamic(
            custom_llm_provider=custom_llm_provider
        )
        
        if not multi_proxy_config:
            verbose_proxy_logger.warning(f"⚠️  No multi-proxy configuration found for {custom_llm_provider}")
            raise Exception(f"No multi-proxy configuration found for {custom_llm_provider}")
        
        proxies = multi_proxy_config.get("proxies", [])
        # 🔧 高并发优化：减少重试次数，避免连接池耗尽
        retry_count = min(multi_proxy_config.get("retry_count", 3), len(proxies))  # 重试次数不超过代理数量
        retry_delay = max(multi_proxy_config.get("retry_delay", 1.0), 0.1)  # 最小延迟0.1秒
        
        verbose_proxy_logger.info(f"📋 Multi-proxy config: {len(proxies)} proxies, {retry_count} retries, {retry_delay}s delay")
        
        last_exception = None
        
        for attempt in range(retry_count):
            verbose_proxy_logger.info(f"🎯 Attempt {attempt + 1}/{retry_count} for {custom_llm_provider}")
            
            # Get next available proxy dynamically
            proxy_config = await global_proxy_config.get_next_available_proxy_dynamic(
                custom_llm_provider=custom_llm_provider,
                prisma_client=None  # Will use global client
            )
            
            if not proxy_config:
                verbose_proxy_logger.error(f"💀 No available proxies for {custom_llm_provider} on attempt {attempt + 1}")
                if attempt < retry_count - 1:
                    verbose_proxy_logger.info(f"⏳ Waiting {retry_delay}s before next attempt...")
                    await asyncio.sleep(retry_delay)
                continue
            
            try:
                proxy_url = proxy_config.get('http') or proxy_config.get('https', 'Unknown')
                verbose_proxy_logger.info(f"🔗 [VPN ATTEMPT] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count} | Selected Proxy: {proxy_url}")
                print(f"🔗 [VPN ATTEMPT] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count} | Selected Proxy: {proxy_url}")
                
                # Execute request with selected proxy - success/failure logging happens in HTTP handler
                response = await func(proxy_config)
                
                # 🎯 请求成功，但具体使用的代理由HTTP handler记录
                verbose_proxy_logger.info(f"✅ [VPN REQUEST SUCCESS] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count}")
                print(f"✅ [VPN REQUEST SUCCESS] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count}")
                return response
                
            except Exception as e:
                last_exception = e
                error_msg = str(e).lower()
                proxy_url = proxy_config.get('http') or proxy_config.get('https', 'Unknown')
                
                # Check if this is a connection-related error
                if any(conn_error in error_msg for conn_error in [
                    "connection", "timeout", "network", "unreachable", 
                    "refused", "reset", "broken", "failed to establish",
                    "streamclosed", "stream closed", "stream has been closed",
                    "attempted to read or stream content", "connection lost",
                    "connection reset", "connection aborted", "proxy error",
                    "502 bad gateway", "503 service unavailable", "504 gateway timeout",
                    "pool", "exhausted", "limit"  # 🔧 添加连接池相关错误
                ]):
                    # 🎯 代理连接失败日志
                    verbose_proxy_logger.warning(f"❌ [VPN ATTEMPT FAILED] Provider: {custom_llm_provider} | Selected Proxy: {proxy_url} | Attempt: {attempt + 1}/{retry_count}")
                    print(f"❌ [VPN ATTEMPT FAILED] Provider: {custom_llm_provider} | Selected Proxy: {proxy_url} | Attempt: {attempt + 1}/{retry_count} | Error: {str(e)[:100]}...")
                    verbose_proxy_logger.debug(f"   [VPN ATTEMPT FAILED] Detailed Error: {e}")
                    
                    # Mark this proxy as failed
                    await self._mark_proxy_as_failed(custom_llm_provider, proxy_url)
                    
                    if attempt < retry_count - 1:
                        # 🔧 高并发优化：动态调整延迟时间
                        dynamic_delay = retry_delay * (1 + attempt * 0.5)  # 递增延迟
                        verbose_proxy_logger.info(f"🔄 [VPN RETRY] Switching to next proxy in {dynamic_delay:.1f} seconds... ({attempt + 2}/{retry_count})")
                        print(f"🔄 [VPN RETRY] Provider: {custom_llm_provider} | Next attempt in {dynamic_delay:.1f}s | Remaining attempts: {retry_count - attempt - 1}")
                        await asyncio.sleep(dynamic_delay)
                    continue
                else:
                    # Non-connection error, don't retry
                    verbose_proxy_logger.error(f"❌ [VPN ERROR] Non-connection error for {custom_llm_provider}: {e}")
                    print(f"❌ [VPN ERROR] Provider: {custom_llm_provider} | Non-connection error: {str(e)[:100]}...")
                    raise e
        
        # All attempts failed - this triggers fallback to no-proxy
        verbose_proxy_logger.error(f"💀 [VPN EXHAUSTED] All proxy attempts failed for {custom_llm_provider} - falling back to direct connection")
        print(f"💀 [VPN EXHAUSTED] Provider: {custom_llm_provider} | All {retry_count} proxies failed | Fallback: Direct connection")
        if last_exception:
            raise last_exception
        else:
            raise Exception(f"All proxy connection attempts failed for {custom_llm_provider}")

    def execute_with_proxy_retry_sync(
        self,
        func: Callable,
        custom_llm_provider: str,
        proxy_config: Dict[str, Any]
    ) -> Any:
        """
        Synchronous version of execute_with_proxy_retry using dynamic configuration
        Execute request with proxy retry mechanism for sync calls
        
        Args:
            func: Sync function that takes proxy_config and makes the request
            custom_llm_provider: Provider name (e.g., 'openrouter')
            proxy_config: Multi-proxy configuration (not used, loaded dynamically)
            
        Returns:
            Response from successful request
            
        Raises:
            Exception: If all proxies fail
        """
        verbose_proxy_logger.info(f"🔄 Starting sync multi-proxy retry for {custom_llm_provider}")
        
        # 🔧 修复asyncio事件循环问题 - 使用已有的配置而不是重新加载
        # Get fresh configuration from database (sync version)
        multi_proxy_config = None
        try:
            # 尝试获取当前事件循环
            import asyncio
            loop = asyncio.get_running_loop()
            # 如果有运行中的事件循环，使用同步方法
            verbose_proxy_logger.debug(f"Running event loop detected, using sync proxy config method")
            multi_proxy_config = global_proxy_config.get_multi_proxy_config(
                custom_llm_provider=custom_llm_provider
            )
        except RuntimeError:
            # 没有运行中的事件循环，可以使用 asyncio.run
            verbose_proxy_logger.debug(f"No running event loop, using asyncio.run")
            import asyncio
            multi_proxy_config = asyncio.run(
                global_proxy_config.get_multi_proxy_config_dynamic(
                    custom_llm_provider=custom_llm_provider
                )
            )
        
        if not multi_proxy_config:
            verbose_proxy_logger.warning(f"⚠️  No multi-proxy configuration found for {custom_llm_provider}")
            raise Exception(f"No multi-proxy configuration found for {custom_llm_provider}")
        
        proxies = multi_proxy_config.get("proxies", [])
        # 🔧 高并发优化：减少重试次数，避免连接池耗尽
        retry_count = min(multi_proxy_config.get("retry_count", 3), len(proxies))  # 重试次数不超过代理数量
        retry_delay = max(multi_proxy_config.get("retry_delay", 1.0), 0.1)  # 最小延迟0.1秒
        
        verbose_proxy_logger.info(f"📋 Sync multi-proxy config: {len(proxies)} proxies, {retry_count} retries, {retry_delay}s delay")
        
        last_exception = None
        
        for attempt in range(retry_count):
            verbose_proxy_logger.info(f"🎯 Sync attempt {attempt + 1}/{retry_count} for {custom_llm_provider}")
            
            # Get next available proxy dynamically (sync version)
            proxy_config = asyncio.run(
                global_proxy_config.get_next_available_proxy_dynamic(
                    custom_llm_provider=custom_llm_provider,
                    prisma_client=None  # Will use global client
                )
            )
            
            if not proxy_config:
                verbose_proxy_logger.error(f"💀 No available proxies for {custom_llm_provider} on sync attempt {attempt + 1}")
                if attempt < retry_count - 1:
                    verbose_proxy_logger.info(f"⏳ Waiting {retry_delay}s before next sync attempt...")
                    time.sleep(retry_delay)
                continue
            
            try:
                proxy_url = proxy_config.get('http') or proxy_config.get('https', 'Unknown')
                verbose_proxy_logger.info(f"🔗 [VPN ATTEMPT SYNC] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count} | Selected Proxy: {proxy_url}")
                print(f"🔗 [VPN ATTEMPT SYNC] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count} | Selected Proxy: {proxy_url}")
                
                # Execute request with selected proxy - success/failure logging happens in HTTP handler
                response = func(proxy_config)
                
                # 🎯 请求成功，但具体使用的代理由HTTP handler记录
                verbose_proxy_logger.info(f"✅ [VPN REQUEST SUCCESS SYNC] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count}")
                print(f"✅ [VPN REQUEST SUCCESS SYNC] Provider: {custom_llm_provider} | Attempt: {attempt + 1}/{retry_count}")
                return response
                
            except Exception as e:
                last_exception = e
                error_msg = str(e).lower()
                proxy_url = proxy_config.get('http') or proxy_config.get('https', 'Unknown')
                
                # Check if this is a connection-related error
                if any(conn_error in error_msg for conn_error in [
                    "connection", "timeout", "network", "unreachable", 
                    "refused", "reset", "broken", "failed to establish",
                    "streamclosed", "stream closed", "stream has been closed",
                    "attempted to read or stream content", "connection lost",
                    "connection reset", "connection aborted", "proxy error",
                    "502 bad gateway", "503 service unavailable", "504 gateway timeout",
                    "pool", "exhausted", "limit"  # 🔧 添加连接池相关错误
                ]):
                    # 🎯 代理连接失败日志
                    verbose_proxy_logger.warning(f"❌ [VPN ATTEMPT FAILED SYNC] Provider: {custom_llm_provider} | Selected Proxy: {proxy_url} | Attempt: {attempt + 1}/{retry_count}")
                    print(f"❌ [VPN ATTEMPT FAILED SYNC] Provider: {custom_llm_provider} | Selected Proxy: {proxy_url} | Attempt: {attempt + 1}/{retry_count} | Error: {str(e)[:100]}...")
                    verbose_proxy_logger.debug(f"   [VPN ATTEMPT FAILED SYNC] Detailed Error: {e}")
                    
                    # Mark this proxy as failed (sync version)
                    self._mark_proxy_as_failed_sync(custom_llm_provider, proxy_url)
                    
                    if attempt < retry_count - 1:
                        # 🔧 高并发优化：动态调整延迟时间
                        dynamic_delay = retry_delay * (1 + attempt * 0.5)  # 递增延迟
                        verbose_proxy_logger.info(f"🔄 [VPN RETRY SYNC] Switching to next proxy in {dynamic_delay:.1f} seconds... ({attempt + 2}/{retry_count})")
                        print(f"🔄 [VPN RETRY SYNC] Provider: {custom_llm_provider} | Next attempt in {dynamic_delay:.1f}s | Remaining attempts: {retry_count - attempt - 1}")
                        time.sleep(dynamic_delay)
                    continue
                else:
                    # Non-connection error, don't retry
                    verbose_proxy_logger.error(f"❌ [VPN ERROR SYNC] Non-connection error for {custom_llm_provider}: {e}")
                    print(f"❌ [VPN ERROR SYNC] Provider: {custom_llm_provider} | Non-connection error: {str(e)[:100]}...")
                    raise e
        
        # All attempts failed - this triggers fallback to no-proxy
        verbose_proxy_logger.error(f"💀 [VPN EXHAUSTED SYNC] All sync proxy attempts failed for {custom_llm_provider} - falling back to direct connection")
        print(f"💀 [VPN EXHAUSTED SYNC] Provider: {custom_llm_provider} | All {retry_count} proxies failed | Fallback: Direct connection")
        if last_exception:
            raise last_exception
        else:
            raise Exception(f"All proxy connection attempts failed for {custom_llm_provider}")

    async def _mark_proxy_as_failed(self, provider: str, proxy_url: str):
        """Mark a proxy as failed in the cache"""
        cache_key = f"failed_proxies_{provider}"
        failed_proxies = self._failed_proxy_cache.get(cache_key, set())
        failed_proxies.add(proxy_url)
        self._failed_proxy_cache[cache_key] = failed_proxies
        verbose_proxy_logger.warning(f"🚫 Marked proxy as failed: {proxy_url} for {provider}")

    def _mark_proxy_as_failed_sync(self, provider: str, proxy_url: str):
        """Synchronous version of _mark_proxy_as_failed"""
        cache_key = f"failed_proxies_{provider}"
        failed_proxies = self._failed_proxy_cache.get(cache_key, set())
        failed_proxies.add(proxy_url)
        self._failed_proxy_cache[cache_key] = failed_proxies
        verbose_proxy_logger.warning(f"🚫 Marked proxy as failed: {proxy_url} for {provider}")


# Global multi-proxy handler instance
multi_proxy_handler = MultiProxyHandler() 