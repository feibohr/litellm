from typing import Optional, Union

import httpx

try:
    from litellm._version import version
except Exception:
    version = "0.0.0"

headers = {
    "User-Agent": f"litellm/{version}",
}


class HTTPHandler:
    def __init__(self, concurrent_limit=1000):
        # 🔧 多代理环境下的连接池优化
        # 检测是否为多代理环境
        is_multi_proxy_env = False
        try:
            from litellm.proxy.proxy_config import global_proxy_config
            # 检查是否存在多代理配置
            if hasattr(global_proxy_config, 'provider_proxy_config'):
                for provider_config in global_proxy_config.provider_proxy_config.values():
                    if isinstance(provider_config, dict) and "proxies" in provider_config:
                        is_multi_proxy_env = True
                        break
        except Exception:
            pass
        
        # 多代理环境下进一步减少连接池大小
        if is_multi_proxy_env:
            # 多代理环境：连接池大小减少到1/8，最少50个
            max_keepalive = max(concurrent_limit // 8, 50)
            from litellm._logging import verbose_logger
            verbose_logger.debug(f"🔄 Multi-proxy environment detected, reducing httpx_handler keepalive connections to {max_keepalive}")
        else:
            # 单代理环境：连接池大小为1/4，最少100个  
            max_keepalive = max(concurrent_limit // 4, 100)

        # Create a client with a connection pool
        self.client = httpx.AsyncClient(
            limits=httpx.Limits(
                max_connections=concurrent_limit,
                max_keepalive_connections=max_keepalive,
            ),
            headers=headers,
        )

    async def close(self):
        # Close the client when you're done with it
        await self.client.aclose()

    async def get(
        self, url: str, params: Optional[dict] = None, headers: Optional[dict] = None
    ):
        response = await self.client.get(url, params=params, headers=headers)
        return response

    async def post(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
    ):
        try:
            response = await self.client.post(
                url, data=data, params=params, headers=headers  # type: ignore
            )
            return response
        except Exception as e:
            raise e
