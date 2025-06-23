import asyncio
import os
import ssl
import time
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional, Union

import httpx
from aiohttp import ClientSession, TCPConnector
from httpx import USE_CLIENT_DEFAULT, AsyncHTTPTransport, HTTPTransport
from httpx._types import RequestFiles

import litellm
from litellm._logging import verbose_logger, verbose_proxy_logger
from litellm.constants import _DEFAULT_TTL_FOR_HTTPX_CLIENTS
from litellm.litellm_core_utils.logging_utils import track_llm_api_timing
from litellm.types.llms.custom_http import *

if TYPE_CHECKING:
    from litellm import LlmProviders
    from litellm.litellm_core_utils.litellm_logging import (
        Logging as LiteLLMLoggingObject,
    )
    from litellm.llms.custom_httpx.aiohttp_transport import LiteLLMAiohttpTransport
else:
    LlmProviders = Any
    LiteLLMLoggingObject = Any
    LiteLLMAiohttpTransport = Any

try:
    from litellm._version import version
except Exception:
    version = "0.0.0"

headers = {
    "User-Agent": f"litellm/{version}",
}

# https://www.python-httpx.org/advanced/timeouts
_DEFAULT_TIMEOUT = httpx.Timeout(timeout=5.0, connect=5.0)


def mask_sensitive_info(error_message):
    # Find the start of the key parameter
    if isinstance(error_message, str):
        key_index = error_message.find("key=")
    else:
        return error_message

    # If key is found
    if key_index != -1:
        # Find the end of the key parameter (next & or end of string)
        next_param = error_message.find("&", key_index)

        if next_param == -1:
            # If no more parameters, mask until the end of the string
            masked_message = error_message[: key_index + 4] + "[REDACTED_API_KEY]"
        else:
            # Replace the key with redacted value, keeping other parameters
            masked_message = (
                error_message[: key_index + 4]
                + "[REDACTED_API_KEY]"
                + error_message[next_param:]
            )

        return masked_message

    return error_message


class MaskedHTTPStatusError(httpx.HTTPStatusError):
    def __init__(
        self, original_error, message: Optional[str] = None, text: Optional[str] = None
    ):
        # Create a new error with the masked URL
        masked_url = mask_sensitive_info(str(original_error.request.url))
        # Create a new error that looks like the original, but with a masked URL

        super().__init__(
            message=original_error.message,
            request=httpx.Request(
                method=original_error.request.method,
                url=masked_url,
                headers=original_error.request.headers,
                content=original_error.request.content,
            ),
            response=httpx.Response(
                status_code=original_error.response.status_code,
                content=original_error.response.content,
                headers=original_error.response.headers,
            ),
        )
        self.message = message
        self.text = text


class AsyncHTTPHandler:
    def __init__(
        self,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        event_hooks: Optional[Mapping[str, List[Callable[..., Any]]]] = None,
        concurrent_limit=1000,
        client_alias: Optional[str] = None,  # name for client in logs
        ssl_verify: Optional[VerifyTypes] = None,
        proxies: Optional[Dict[str, str]] = None,
    ):
        self.timeout = timeout
        self.event_hooks = event_hooks
        self.proxies = proxies
        self.client = self.create_client(
            timeout=timeout,
            concurrent_limit=concurrent_limit,
            event_hooks=event_hooks,
            ssl_verify=ssl_verify,
            proxies=proxies,
        )
        self.client_alias = client_alias

    def create_client(
        self,
        timeout: Optional[Union[float, httpx.Timeout]],
        concurrent_limit: int,
        event_hooks: Optional[Mapping[str, List[Callable[..., Any]]]],
        ssl_verify: Optional[VerifyTypes] = None,
        proxies: Optional[Dict[str, str]] = None,
    ) -> httpx.AsyncClient:
        # SSL certificates (a.k.a CA bundle) used to verify the identity of requested hosts.
        # /path/to/certificate.pem
        if ssl_verify is None:
            ssl_verify = os.getenv("SSL_VERIFY", litellm.ssl_verify)

        ssl_security_level = os.getenv("SSL_SECURITY_LEVEL")

        # If ssl_verify is not False and we need a lower security level
        if (
            not ssl_verify
            and ssl_security_level
            and isinstance(ssl_security_level, str)
        ):
            # Create a custom SSL context with reduced security level
            custom_ssl_context = ssl.create_default_context()
            custom_ssl_context.set_ciphers(ssl_security_level)

            # If ssl_verify is a path to a CA bundle, load it into our custom context
            if isinstance(ssl_verify, str) and os.path.exists(ssl_verify):
                custom_ssl_context.load_verify_locations(cafile=ssl_verify)

            # Use our custom SSL context instead of the original ssl_verify value
            ssl_verify = custom_ssl_context

        # An SSL certificate used by the requested host to authenticate the client.
        # /path/to/client.pem
        cert = os.getenv("SSL_CERTIFICATE", litellm.ssl_certificate)

        if timeout is None:
            timeout = _DEFAULT_TIMEOUT
        # Create a client with a connection pool

        transport = AsyncHTTPHandler._create_async_transport(
            ssl_context=ssl_verify if isinstance(ssl_verify, ssl.SSLContext) else None,
            ssl_verify=ssl_verify if isinstance(ssl_verify, bool) else None,
        )

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
            verbose_logger.debug(f"🔄 Multi-proxy environment detected, reducing keepalive connections to {max_keepalive}")
        else:
            # 单代理环境：连接池大小为1/4，最少100个  
            max_keepalive = max(concurrent_limit // 4, 100)

        return httpx.AsyncClient(
            transport=transport,
            event_hooks=event_hooks,
            timeout=timeout,
            limits=httpx.Limits(
                max_connections=concurrent_limit,
                max_keepalive_connections=max_keepalive,
            ),
            verify=ssl_verify,
            cert=cert,
            headers=headers,
            proxies=proxies,
        )

    async def close(self):
        # Close the client when you're done with it
        await self.client.aclose()

    async def __aenter__(self):
        return self.client

    async def __aexit__(self):
        # close the client when exiting
        await self.client.aclose()

    async def get(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        follow_redirects: Optional[bool] = None,
    ):
        # Set follow_redirects to UseClientDefault if None
        _follow_redirects = (
            follow_redirects if follow_redirects is not None else USE_CLIENT_DEFAULT
        )

        params = params or {}
        params.update(HTTPHandler.extract_query_params(url))

        response = await self.client.get(
            url, params=params, headers=headers, follow_redirects=_follow_redirects  # type: ignore
        )
        return response

    @track_llm_api_timing()
    async def post(
        self,
        url: str,
        data: Optional[Union[dict, str, bytes]] = None,  # type: ignore
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        stream: bool = False,
        logging_obj: Optional[LiteLLMLoggingObject] = None,
        files: Optional[RequestFiles] = None,
    ):
        start_time = time.time()
        try:
            if timeout is None:
                timeout = self.timeout

            req = self.client.build_request(
                "POST",
                url,
                data=data,  # type: ignore
                json=json,
                params=params,
                headers=headers,
                timeout=timeout,
                files=files,
            )
            response = await self.client.send(req, stream=stream)
            response.raise_for_status()
            return response
        except (httpx.RemoteProtocolError, httpx.ConnectError):
            # Retry the request with a new session if there is a connection error
            new_client = self.create_client(
                timeout=timeout, concurrent_limit=1, event_hooks=self.event_hooks, proxies=self.proxies
            )
            try:
                return await self.single_connection_post_request(
                    url=url,
                    client=new_client,
                    data=data,
                    json=json,
                    params=params,
                    headers=headers,
                    stream=stream,
                )
            finally:
                await new_client.aclose()
        except httpx.TimeoutException as e:
            end_time = time.time()
            time_delta = round(end_time - start_time, 3)
            headers = {}
            error_response = getattr(e, "response", None)
            if error_response is not None:
                for key, value in error_response.headers.items():
                    headers["response_headers-{}".format(key)] = value

            raise litellm.Timeout(
                message=f"Connection timed out. Timeout passed={timeout}, time taken={time_delta} seconds",
                model="default-model-name",
                llm_provider="litellm-httpx-handler",
                headers=headers,
            )
        except httpx.HTTPStatusError as e:
            if stream is True:
                setattr(e, "message", await e.response.aread())
                setattr(e, "text", await e.response.aread())
            else:
                setattr(e, "message", mask_sensitive_info(e.response.text))
                setattr(e, "text", mask_sensitive_info(e.response.text))

            setattr(e, "status_code", e.response.status_code)

            raise e
        except Exception as e:
            raise e

    async def put(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,  # type: ignore
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        stream: bool = False,
    ):
        try:
            if timeout is None:
                timeout = self.timeout

            req = self.client.build_request(
                "PUT", url, data=data, json=json, params=params, headers=headers, timeout=timeout  # type: ignore
            )
            response = await self.client.send(req)
            response.raise_for_status()
            return response
        except (httpx.RemoteProtocolError, httpx.ConnectError):
            # Retry the request with a new session if there is a connection error
            new_client = self.create_client(
                timeout=timeout, concurrent_limit=1, event_hooks=self.event_hooks, proxies=self.proxies
            )
            try:
                return await self.single_connection_post_request(
                    url=url,
                    client=new_client,
                    data=data,
                    json=json,
                    params=params,
                    headers=headers,
                    stream=stream,
                )
            finally:
                await new_client.aclose()
        except httpx.TimeoutException as e:
            headers = {}
            error_response = getattr(e, "response", None)
            if error_response is not None:
                for key, value in error_response.headers.items():
                    headers["response_headers-{}".format(key)] = value

            raise litellm.Timeout(
                message=f"Connection timed out after {timeout} seconds.",
                model="default-model-name",
                llm_provider="litellm-httpx-handler",
                headers=headers,
            )
        except httpx.HTTPStatusError as e:
            setattr(e, "status_code", e.response.status_code)
            if stream is True:
                setattr(e, "message", await e.response.aread())
            else:
                setattr(e, "message", e.response.text)
            raise e
        except Exception as e:
            raise e

    async def patch(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,  # type: ignore
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        stream: bool = False,
    ):
        try:
            if timeout is None:
                timeout = self.timeout

            req = self.client.build_request(
                "PATCH", url, data=data, json=json, params=params, headers=headers, timeout=timeout  # type: ignore
            )
            response = await self.client.send(req)
            response.raise_for_status()
            return response
        except (httpx.RemoteProtocolError, httpx.ConnectError):
            # Retry the request with a new session if there is a connection error
            new_client = self.create_client(
                timeout=timeout, concurrent_limit=1, event_hooks=self.event_hooks, proxies=self.proxies
            )
            try:
                return await self.single_connection_post_request(
                    url=url,
                    client=new_client,
                    data=data,
                    json=json,
                    params=params,
                    headers=headers,
                    stream=stream,
                )
            finally:
                await new_client.aclose()
        except httpx.TimeoutException as e:
            headers = {}
            error_response = getattr(e, "response", None)
            if error_response is not None:
                for key, value in error_response.headers.items():
                    headers["response_headers-{}".format(key)] = value

            raise litellm.Timeout(
                message=f"Connection timed out after {timeout} seconds.",
                model="default-model-name",
                llm_provider="litellm-httpx-handler",
                headers=headers,
            )
        except httpx.HTTPStatusError as e:
            setattr(e, "status_code", e.response.status_code)
            if stream is True:
                setattr(e, "message", await e.response.aread())
            else:
                setattr(e, "message", e.response.text)
            raise e
        except Exception as e:
            raise e

    async def delete(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,  # type: ignore
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        stream: bool = False,
    ):
        try:
            if timeout is None:
                timeout = self.timeout
            req = self.client.build_request(
                "DELETE", url, data=data, json=json, params=params, headers=headers, timeout=timeout  # type: ignore
            )
            response = await self.client.send(req, stream=stream)
            response.raise_for_status()
            return response
        except (httpx.RemoteProtocolError, httpx.ConnectError):
            # Retry the request with a new session if there is a connection error
            new_client = self.create_client(
                timeout=timeout, concurrent_limit=1, event_hooks=self.event_hooks, proxies=self.proxies
            )
            try:
                return await self.single_connection_post_request(
                    url=url,
                    client=new_client,
                    data=data,
                    json=json,
                    params=params,
                    headers=headers,
                    stream=stream,
                )
            finally:
                await new_client.aclose()
        except httpx.HTTPStatusError as e:
            setattr(e, "status_code", e.response.status_code)
            if stream is True:
                setattr(e, "message", await e.response.aread())
            else:
                setattr(e, "message", e.response.text)
            raise e
        except Exception as e:
            raise e

    async def single_connection_post_request(
        self,
        url: str,
        client: httpx.AsyncClient,
        data: Optional[Union[dict, str, bytes]] = None,  # type: ignore
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        stream: bool = False,
    ):
        """
        Making POST request for a single connection client.

        Used for retrying connection client errors.
        """
        req = client.build_request(
            "POST", url, data=data, json=json, params=params, headers=headers  # type: ignore
        )
        response = await client.send(req, stream=stream)
        response.raise_for_status()
        return response

    def __del__(self) -> None:
        try:
            asyncio.get_running_loop().create_task(self.close())
        except Exception:
            pass

    @staticmethod
    def _create_async_transport(
        ssl_context: Optional[ssl.SSLContext] = None, ssl_verify: Optional[bool] = None
    ) -> Optional[Union[LiteLLMAiohttpTransport, AsyncHTTPTransport]]:
        """
        - Creates a transport for httpx.AsyncClient
            - if litellm.force_ipv4 is True, it will return AsyncHTTPTransport with local_address="0.0.0.0"
            - [Default] It will return AiohttpTransport
            - Users can opt out of using AiohttpTransport by setting litellm.use_aiohttp_transport to False


        Notes on this handler:
        - Why AiohttpTransport?
            - By default, we use AiohttpTransport since it offers much higher throughput and lower latency than httpx.

        - Why force ipv4?
            - Some users have seen httpx ConnectionError when using ipv6 - forcing ipv4 resolves the issue for them
        """
        #########################################################
        # AIOHTTP TRANSPORT is off by default
        #########################################################
        if AsyncHTTPHandler._should_use_aiohttp_transport():
            return AsyncHTTPHandler._create_aiohttp_transport(
                ssl_context=ssl_context, ssl_verify=ssl_verify
            )

        #########################################################
        # HTTPX TRANSPORT is used when aiohttp is not installed
        #########################################################
        return AsyncHTTPHandler._create_httpx_transport()

    @staticmethod
    def _should_use_aiohttp_transport() -> bool:
        """
        AiohttpTransport is the default transport for litellm.

        Httpx can be used by the following
            - litellm.disable_aiohttp_transport = True
            - os.getenv("DISABLE_AIOHTTP_TRANSPORT") = "True"
        """
        import os

        from litellm.secret_managers.main import str_to_bool

        #########################################################
        # Check if user disabled aiohttp transport
        ########################################################
        if (
            litellm.disable_aiohttp_transport is True
            or str_to_bool(os.getenv("DISABLE_AIOHTTP_TRANSPORT", "False")) is True
        ):
            return False

        #########################################################
        # Default: Use AiohttpTransport
        ########################################################
        verbose_logger.debug("Using AiohttpTransport...")
        return True

    @staticmethod
    def _create_aiohttp_transport(
        ssl_verify: Optional[bool] = None,
        ssl_context: Optional[ssl.SSLContext] = None,
    ) -> LiteLLMAiohttpTransport:
        """
        Creates an AiohttpTransport with RequestNotRead error handling

        - If force_ipv4 is True, it will create an AiohttpTransport with local_addr set to "0.0.0.0"
        - [Default] If force_ipv4 is False, it will create an AiohttpTransport with default settings
        """
        from litellm.llms.custom_httpx.aiohttp_transport import LiteLLMAiohttpTransport

        #########################################################
        # If ssl_verify is None, set it to True
        # TCP Connector does not allow ssl_verify to be None
        # by default aiohttp sets ssl_verify to True
        #########################################################
        if ssl_verify is None:
            ssl_verify = True

        verbose_logger.debug("Creating AiohttpTransport...")
        return LiteLLMAiohttpTransport(
            client=lambda: ClientSession(
                connector=TCPConnector(
                    verify_ssl=ssl_verify,
                    ssl_context=ssl_context,
                    local_addr=("0.0.0.0", 0) if litellm.force_ipv4 else None,
                )
            ),
        )

    @staticmethod
    def _create_httpx_transport() -> Optional[AsyncHTTPTransport]:
        """
        Creates an AsyncHTTPTransport

        - If force_ipv4 is True, it will create an AsyncHTTPTransport with local_address set to "0.0.0.0"
        - [Default] If force_ipv4 is False, it will return None
        """
        if litellm.force_ipv4:
            return AsyncHTTPTransport(local_address="0.0.0.0")
        else:
            return None


class HTTPHandler:
    def __init__(
        self,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        concurrent_limit=1000,
        client: Optional[httpx.Client] = None,
        ssl_verify: Optional[Union[bool, str]] = None,
        proxies: Optional[Dict[str, str]] = None,
    ):
        if timeout is None:
            timeout = _DEFAULT_TIMEOUT

        # SSL certificates (a.k.a CA bundle) used to verify the identity of requested hosts.
        # /path/to/certificate.pem

        if ssl_verify is None:
            ssl_verify = os.getenv("SSL_VERIFY", litellm.ssl_verify)

        # An SSL certificate used by the requested host to authenticate the client.
        # /path/to/client.pem
        cert = os.getenv("SSL_CERTIFICATE", litellm.ssl_certificate)

        if client is None:
            transport = self._create_sync_transport()

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
                verbose_logger.debug(f"🔄 Multi-proxy environment detected, reducing sync keepalive connections to {max_keepalive}")
            else:
                # 单代理环境：连接池大小为1/4，最少100个  
                max_keepalive = max(concurrent_limit // 4, 100)

            # Create a client with a connection pool
            self.client = httpx.Client(
                transport=transport,
                timeout=timeout,
                limits=httpx.Limits(
                    max_connections=concurrent_limit,
                    max_keepalive_connections=max_keepalive,
                ),
                verify=ssl_verify,
                cert=cert,
                headers=headers,
                proxies=proxies,
            )
        else:
            self.client = client

    def close(self):
        # Close the client when you're done with it
        self.client.close()

    def get(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        follow_redirects: Optional[bool] = None,
    ):
        # Set follow_redirects to UseClientDefault if None
        _follow_redirects = (
            follow_redirects if follow_redirects is not None else USE_CLIENT_DEFAULT
        )
        params = params or {}
        params.update(self.extract_query_params(url))

        response = self.client.get(
            url, params=params, headers=headers, follow_redirects=_follow_redirects  # type: ignore
        )

        return response

    @staticmethod
    def extract_query_params(url: str) -> Dict[str, str]:
        """
        Parse a URL's query-string into a dict.

        :param url: full URL, e.g. "https://.../path?foo=1&bar=2"
        :return: {"foo": "1", "bar": "2"}
        """
        from urllib.parse import parse_qsl, urlsplit

        parts = urlsplit(url)
        return dict(parse_qsl(parts.query))

    def post(
        self,
        url: str,
        data: Optional[Union[dict, str, bytes]] = None,
        json: Optional[Union[dict, str, List]] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        stream: bool = False,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        files: Optional[Union[dict, RequestFiles]] = None,
        content: Any = None,
        logging_obj: Optional[LiteLLMLoggingObject] = None,
    ):
        try:
            if timeout is not None:
                req = self.client.build_request(
                    "POST",
                    url,
                    data=data,  # type: ignore
                    json=json,
                    params=params,
                    headers=headers,
                    timeout=timeout,
                    files=files,
                    content=content,  # type: ignore
                )
            else:
                req = self.client.build_request(
                    "POST", url, data=data, json=json, params=params, headers=headers, files=files, content=content  # type: ignore
                )
            response = self.client.send(req, stream=stream)
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise litellm.Timeout(
                message=f"Connection timed out after {timeout} seconds.",
                model="default-model-name",
                llm_provider="litellm-httpx-handler",
            )
        except httpx.HTTPStatusError as e:
            if stream is True:
                setattr(e, "message", mask_sensitive_info(e.response.read()))
                setattr(e, "text", mask_sensitive_info(e.response.read()))
            else:
                error_text = mask_sensitive_info(e.response.text)
                setattr(e, "message", error_text)
                setattr(e, "text", error_text)

            setattr(e, "status_code", e.response.status_code)
            raise e
        except Exception as e:
            raise e

    def patch(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,
        json: Optional[Union[dict, str]] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        stream: bool = False,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
    ):
        try:
            if timeout is not None:
                req = self.client.build_request(
                    "PATCH", url, data=data, json=json, params=params, headers=headers, timeout=timeout  # type: ignore
                )
            else:
                req = self.client.build_request(
                    "PATCH", url, data=data, json=json, params=params, headers=headers  # type: ignore
                )
            response = self.client.send(req, stream=stream)
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise litellm.Timeout(
                message=f"Connection timed out after {timeout} seconds.",
                model="default-model-name",
                llm_provider="litellm-httpx-handler",
            )
        except httpx.HTTPStatusError as e:
            if stream is True:
                setattr(e, "message", mask_sensitive_info(e.response.read()))
                setattr(e, "text", mask_sensitive_info(e.response.read()))
            else:
                error_text = mask_sensitive_info(e.response.text)
                setattr(e, "message", error_text)
                setattr(e, "text", error_text)

            setattr(e, "status_code", e.response.status_code)

            raise e
        except Exception as e:
            raise e

    def put(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,
        json: Optional[Union[dict, str]] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        stream: bool = False,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
    ):
        try:
            if timeout is not None:
                req = self.client.build_request(
                    "PUT", url, data=data, json=json, params=params, headers=headers, timeout=timeout  # type: ignore
                )
            else:
                req = self.client.build_request(
                    "PUT", url, data=data, json=json, params=params, headers=headers  # type: ignore
                )
            response = self.client.send(req, stream=stream)
            return response
        except httpx.TimeoutException:
            raise litellm.Timeout(
                message=f"Connection timed out after {timeout} seconds.",
                model="default-model-name",
                llm_provider="litellm-httpx-handler",
            )
        except Exception as e:
            raise e

    def delete(
        self,
        url: str,
        data: Optional[Union[dict, str]] = None,  # type: ignore
        json: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[Union[float, httpx.Timeout]] = None,
        stream: bool = False,
    ):
        try:
            if timeout is not None:
                req = self.client.build_request(
                    "DELETE", url, data=data, json=json, params=params, headers=headers, timeout=timeout  # type: ignore
                )
            else:
                req = self.client.build_request(
                    "DELETE", url, data=data, json=json, params=params, headers=headers  # type: ignore
                )
            response = self.client.send(req, stream=stream)
            response.raise_for_status()
            return response
        except httpx.TimeoutException:
            raise litellm.Timeout(
                message=f"Connection timed out after {timeout} seconds.",
                model="default-model-name",
                llm_provider="litellm-httpx-handler",
            )
        except httpx.HTTPStatusError as e:
            if stream is True:
                setattr(e, "message", mask_sensitive_info(e.response.read()))
                setattr(e, "text", mask_sensitive_info(e.response.read()))
            else:
                error_text = mask_sensitive_info(e.response.text)
                setattr(e, "message", error_text)
                setattr(e, "text", error_text)

            setattr(e, "status_code", e.response.status_code)

            raise e
        except Exception as e:
            raise e

    def __del__(self) -> None:
        try:
            self.close()
        except Exception:
            pass

    def _create_sync_transport(self) -> Optional[HTTPTransport]:
        """
        Create an HTTP transport with IPv4 only if litellm.force_ipv4 is True.
        Otherwise, return None.

        Some users have seen httpx ConnectionError when using ipv6 - forcing ipv4 resolves the issue for them
        """
        if litellm.force_ipv4:
            return HTTPTransport(local_address="0.0.0.0")
        else:
            return None


def get_async_httpx_client(
    llm_provider: Union[LlmProviders, httpxSpecialProvider],
    params: Optional[dict] = None,
    custom_llm_provider: Optional[str] = None,
) -> AsyncHTTPHandler:
    """
    Retrieves the async HTTP client from the cache
    If not present, creates a new client

    Caches the new client and returns it.
    """
    verbose_proxy_logger.debug(f"🌐 Creating async HTTP client for {custom_llm_provider or llm_provider}")
    
    _params_key_name = ""
    if params is not None:
        for key, value in params.items():
            try:
                _params_key_name += f"{key}_{value}"
            except Exception:
                pass

    _cache_key_name = "async_httpx_client" + _params_key_name + llm_provider
    _cached_client = litellm.in_memory_llm_clients_cache.get_cache(_cache_key_name)
    if _cached_client:
        return _cached_client

    # Check for multi-proxy configuration first
    multi_proxy_config = None
    try:
        from litellm.proxy.proxy_config import global_proxy_config
        if custom_llm_provider:
            multi_proxy_config = global_proxy_config.get_multi_proxy_config(
                custom_llm_provider=custom_llm_provider
            )
    except Exception as e:
        verbose_logger.debug(f"Could not check multi-proxy config: {e}")
        
    # Get proxy configuration from global proxy config (with error handling)
    proxy_config = None
    try:
        from litellm.proxy.proxy_config import global_proxy_config
        if multi_proxy_config:
            verbose_proxy_logger.info(
                f"🔄 Multi-proxy configuration detected for {custom_llm_provider}, "
                f"will use dynamic proxy selection"
            )
            # For multi-proxy, get the next available proxy
            proxy_config = global_proxy_config.get_next_available_proxy(
                custom_llm_provider=custom_llm_provider
            )
            if proxy_config:
                # Convert to httpx format
                httpx_proxy_config = {}
                if proxy_config.get('http'):
                    httpx_proxy_config['http://'] = proxy_config['http']
                if proxy_config.get('https'):
                    httpx_proxy_config['https://'] = proxy_config['https']
                proxy_config = httpx_proxy_config
        else:
            # Use regular proxy configuration
            proxy_config = global_proxy_config.get_httpx_proxy_config(custom_llm_provider=custom_llm_provider)
    except (ImportError, AttributeError, Exception) as e:
        # If proxy config is not available, continue without proxy
        verbose_logger.debug(f"Proxy config not available: {e}")
        proxy_config = None
        
    # 🔍 添加代理配置日志打印
    if proxy_config:
        proxy_type = "MULTI-PROXY" if multi_proxy_config else "PROXY"
        verbose_proxy_logger.info(f"🌐 [{proxy_type}] Creating Async HTTP client for {custom_llm_provider or llm_provider} with proxy config: {proxy_config}")
        verbose_proxy_logger.debug(f"🌐 [{proxy_type}] Async HTTP Client | Provider: {custom_llm_provider or llm_provider} | Proxy Config: {proxy_config}")
    else:
        verbose_proxy_logger.debug(f"🌐 [PROXY] Creating Async HTTP client for {custom_llm_provider or llm_provider} without proxy")
        verbose_proxy_logger.debug(f"🌐 [PROXY] Async HTTP Client | Provider: {custom_llm_provider or llm_provider} | No proxy configured")

    # Create params dict with proxy config if available
    client_params = {}
    if params is not None:
        client_params.update(params)
    
    # Add proxy config to params if available
    if proxy_config:
        client_params['proxies'] = proxy_config

    # Create params dict with proxy config if available
    httpx_client_params = {}
    if proxy_config:
        httpx_client_params['proxies'] = proxy_config

    # Default timeout and concurrent limit
    timeout = httpx.Timeout(
        timeout=600.0, read=600.0, write=600.0, connect=600.0, pool=600.0
    )

    httpx_client = AsyncHTTPHandler(
        timeout=timeout, concurrent_limit=1000, **httpx_client_params
    )
    litellm.in_memory_llm_clients_cache.set_cache(_cache_key_name, httpx_client)
    return httpx_client


def _get_httpx_client(params: Optional[dict] = None, custom_llm_provider: Optional[str] = None) -> HTTPHandler:
    """
    Retrieves the HTTP client from the cache
    If not present, creates a new client

    Caches the new client and returns it.
    """
    verbose_proxy_logger.debug(f"🌐 Creating sync HTTP client for {custom_llm_provider or 'unknown'}")
    
    _params_key_name = ""
    if params is not None:
        for key, value in params.items():
            try:
                _params_key_name += f"{key}_{value}"
            except Exception:
                pass

    _cache_key_name = "httpx_client" + _params_key_name

    _cached_client = litellm.in_memory_llm_clients_cache.get_cache(_cache_key_name)
    if _cached_client:
        return _cached_client

    # Check for multi-proxy configuration first
    multi_proxy_config = None
    try:
        from litellm.proxy.proxy_config import global_proxy_config
        if custom_llm_provider:
            multi_proxy_config = global_proxy_config.get_multi_proxy_config(
                custom_llm_provider=custom_llm_provider
            )
    except Exception as e:
        verbose_logger.debug(f"Could not check multi-proxy config: {e}")
        
    # Get proxy configuration from global proxy config (with error handling)
    proxy_config = None
    try:
        from litellm.proxy.proxy_config import global_proxy_config
        if multi_proxy_config:
            verbose_proxy_logger.info(
                f"🔄 Multi-proxy configuration detected for {custom_llm_provider}, "
                f"will use dynamic proxy selection"
            )
            # For multi-proxy, get the next available proxy
            proxy_config = global_proxy_config.get_next_available_proxy(
                custom_llm_provider=custom_llm_provider
            )
            if proxy_config:
                # Convert to httpx format
                httpx_proxy_config = {}
                if proxy_config.get('http'):
                    httpx_proxy_config['http://'] = proxy_config['http']
                if proxy_config.get('https'):
                    httpx_proxy_config['https://'] = proxy_config['https']
                proxy_config = httpx_proxy_config
        else:
            # Use regular proxy configuration
            proxy_config = global_proxy_config.get_httpx_proxy_config(custom_llm_provider=custom_llm_provider)
    except (ImportError, AttributeError, Exception) as e:
        # If proxy config is not available, continue without proxy
        verbose_logger.debug(f"Proxy config not available: {e}")
        proxy_config = None
        
    # 🔍 添加代理配置日志打印
    if proxy_config:
        proxy_type = "MULTI-PROXY" if multi_proxy_config else "PROXY"
        verbose_proxy_logger.info(f"🌐 [{proxy_type}] Creating HTTP client for {custom_llm_provider or 'unknown'} with proxy config: {proxy_config}")
        verbose_proxy_logger.debug(f"🌐 [{proxy_type}] HTTP Client | Provider: {custom_llm_provider or 'unknown'} | Proxy Config: {proxy_config}")
    else:
        verbose_proxy_logger.debug(f"🌐 [PROXY] Creating HTTP client for {custom_llm_provider or 'unknown'} without proxy")
        verbose_proxy_logger.debug(f"🌐 [PROXY] HTTP Client | Provider: {custom_llm_provider or 'unknown'} | No proxy configured")

    # Create params dict with proxy config if available
    client_params = {}
    if params is not None:
        client_params.update(params)
    
    # Add proxy config to params if available
    if proxy_config:
        client_params['proxies'] = proxy_config

    # Create params dict with proxy config if available
    httpx_client_params = {}
    if proxy_config:
        httpx_client_params['proxies'] = proxy_config

    # Default timeout and concurrent limit  
    timeout = httpx.Timeout(timeout=600.0, connect=5.0)

    httpx_client = HTTPHandler(timeout=timeout, concurrent_limit=1000, **httpx_client_params)
    litellm.in_memory_llm_clients_cache.set_cache(_cache_key_name, httpx_client)
    return httpx_client


def get_simple_async_httpx_client(
    llm_provider: str,
    custom_llm_provider: Optional[str] = None,
    api_base: Optional[str] = None,
    timeout: Optional[Union[float, httpx.Timeout]] = None,
    **kwargs
) -> httpx.AsyncClient:
    """
    Get async httpx client with proxy configuration if available
    """
    verbose_proxy_logger.debug(f"🌐 Creating async HTTP client for {custom_llm_provider or llm_provider}")
    
    # Get proxy configuration
    try:
        from litellm.proxy.proxy_config import global_proxy_config
        
        # Check for multi-proxy configuration first
        multi_proxy_config = global_proxy_config.get_multi_proxy_config(
            custom_llm_provider=custom_llm_provider
        )
        
        if multi_proxy_config:
            verbose_proxy_logger.info(
                f"🔄 Multi-proxy configuration detected for {custom_llm_provider}, "
                f"will use dynamic proxy selection"
            )
            # For multi-proxy, we don't set a fixed proxy here
            # The proxy will be selected dynamically in the request handler
            proxy_config = None
        else:
            # Get regular proxy configuration
            proxy_config = global_proxy_config.get_httpx_proxy_config(
                custom_llm_provider=custom_llm_provider
            )
            
            if proxy_config:
                verbose_proxy_logger.info(
                    f"🔗 Using single proxy configuration for {custom_llm_provider}: {proxy_config}"
                )
            else:
                verbose_proxy_logger.debug(f"📝 No proxy configuration for {custom_llm_provider}")
                
    except ImportError:
        verbose_proxy_logger.debug("⚠️  Proxy configuration module not available")
        proxy_config = None
    except Exception as e:
        verbose_proxy_logger.warning(f"⚠️  Error getting proxy configuration: {e}")
        proxy_config = None
    
    # Create client configuration
    client_config = {}
    
    if proxy_config:
        client_config['proxies'] = proxy_config
        verbose_proxy_logger.info(f"✅ HTTP client will use proxy: {proxy_config}")
    
    if timeout:
        client_config['timeout'] = timeout
        verbose_proxy_logger.debug(f"⏱️  HTTP client timeout: {timeout}")
    
    # Add any additional kwargs
    client_config.update(kwargs)
    
    verbose_proxy_logger.debug(f"🔧 Final HTTP client config: {client_config}")
    
    return httpx.AsyncClient(**client_config)


def get_simple_sync_httpx_client(
    llm_provider: str,
    custom_llm_provider: Optional[str] = None,
    api_base: Optional[str] = None,
    timeout: Optional[Union[float, httpx.Timeout]] = None,
    **kwargs
) -> httpx.Client:
    """
    Get sync httpx client with proxy configuration if available
    """
    verbose_proxy_logger.debug(f"🌐 Creating sync HTTP client for {custom_llm_provider or llm_provider}")
    
    # Get proxy configuration
    try:
        from litellm.proxy.proxy_config import global_proxy_config
        
        # Check for multi-proxy configuration first
        multi_proxy_config = global_proxy_config.get_multi_proxy_config(
            custom_llm_provider=custom_llm_provider
        )
        
        if multi_proxy_config:
            verbose_proxy_logger.info(
                f"🔄 Multi-proxy configuration detected for {custom_llm_provider}, "
                f"will use dynamic proxy selection"
            )
            # For multi-proxy, we don't set a fixed proxy here
            proxy_config = None
        else:
            # Get regular proxy configuration
            proxy_config = global_proxy_config.get_httpx_proxy_config(
                custom_llm_provider=custom_llm_provider
            )
            
            if proxy_config:
                verbose_proxy_logger.info(
                    f"🔗 Using single proxy configuration for {custom_llm_provider}: {proxy_config}"
                )
            else:
                verbose_proxy_logger.debug(f"📝 No proxy configuration for {custom_llm_provider}")
                
    except ImportError:
        verbose_proxy_logger.debug("⚠️  Proxy configuration module not available")
        proxy_config = None
    except Exception as e:
        verbose_proxy_logger.warning(f"⚠️  Error getting proxy configuration: {e}")
        proxy_config = None
    
    # Create client configuration
    client_config = {}
    
    if proxy_config:
        client_config['proxies'] = proxy_config
        verbose_proxy_logger.info(f"✅ HTTP client will use proxy: {proxy_config}")
    
    if timeout:
        client_config['timeout'] = timeout
        verbose_proxy_logger.debug(f"⏱️  HTTP client timeout: {timeout}")
    
    # Add any additional kwargs
    client_config.update(kwargs)
    
    verbose_proxy_logger.debug(f"🔧 Final HTTP client config: {client_config}")
    
    return httpx.Client(**client_config)
