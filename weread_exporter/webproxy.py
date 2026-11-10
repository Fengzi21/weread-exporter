"""
Web Proxy
"""

import asyncio
import base64
import logging
import re
import sys
import traceback
from typing import Dict, List, Optional, Tuple, Union, Any, Callable, Awaitable, cast  # pyright: ignore[reportDeprecated]

import pyppeteer

from . import utils

if sys.version_info >= (3, 8):
    from typing import TYPE_CHECKING
else:
    from typing_extensions import TYPE_CHECKING


class EnumResourceType(object):
    """资源类型"""

    Document: str = "Document"
    Stylesheet: str = "Stylesheet"
    Image: str = "Image"
    Media: str = "Media"
    Font: str = "Font"
    Script: str = "Script"
    TextTrack: str = "TextTrack"
    XHR: str = "XHR"
    Fetch: str = "Fetch"
    Prefetch: str = "Prefetch"
    EventSource: str = "EventSource"
    WebSocket: str = "WebSocket"
    Manifest: str = "Manifest"
    SignedExchange: str = "SignedExchange"
    Ping: str = "Ping"
    CSPViolationReport: str = "CSPViolationReport"
    Preflight: str = "Preflight"
    FedCM: str = "FedCM"
    Other: str = "Other"


class EnumProxyStage(object):
    """代理阶段"""

    Request: str = "request"
    Response: str = "response"


class EnumProxyType(object):
    """代理类型"""

    Block: int = 1
    Continue: int = 2
    Mock: int = 3


class ProxyRule(object):
    """代理规则基类

    用于定义请求拦截规则和响应处理逻辑
    """

    def __init__(
        self,
        url_pattern: str,
        callback: Callable[
            [Union["WebRequest", "WebResponse"]],  # pyright: ignore[reportDeprecated]
            Union[Dict[str, Any], Awaitable[Dict[str, Any]], None],  # pyright: ignore[reportDeprecated]
        ],
        resource_type: Optional[str] = None,  # pyright: ignore[reportDeprecated]
        stage: Optional[str] = None,  # pyright: ignore[reportDeprecated]
        methods: Optional[List[str]] = None,  # pyright: ignore[reportDeprecated]
    ) -> None:
        """初始化代理规则

        Args:
            url_pattern: URL 匹配模式，支持通配符 *
            callback: 回调函数
            resource_type: 资源类型，如 'Document', 'Script', 'Stylesheet', 'Image', 'XHR', 'Fetch', 'WebSocket' 等
            stage: 代理阶段
            methods: HTTP 方法列表，如 ['GET', 'POST']，None 表示匹配所有方法
        """
        self._url_pattern = url_pattern
        self._callback = callback
        self._resource_type = resource_type
        self._stage = stage or EnumProxyStage.Request
        self._methods = methods or []

    @property
    def stage(self) -> str:
        """拦截阶段"""
        return self._stage

    def matches(self, request: Any) -> bool:  # pyright: ignore[reportUnknownParameterType]
        """检查请求是否匹配此规则

        Args:
            request: pyppeteer 的 Request 对象（用于匹配）

        Returns:
            bool: 是否匹配
        """
        # pyppeteer Request 对象
        url: str = request.url
        method: str = request.method

        # 检查 URL 模式
        if not self._match_url(url):
            return False

        # 检查 HTTP 方法
        if self._methods and method not in self._methods:
            return False

        return True

    def _match_url(self, url: str) -> bool:
        """匹配 URL 模式

        支持 * 通配符，如 '*/web/book/read*' 或 '*/weread.qq.com/*'

        Args:
            url: 实际的 URL

        Returns:
            bool: 是否匹配
        """
        pattern = self._url_pattern.replace("*", ".*")
        # 确保模式匹配完整字符串
        if not pattern.startswith(".*"):
            pattern = "^.*" + pattern
        if not pattern.endswith(".*"):
            pattern = pattern + ".*$"

        return re.match(pattern, url) is not None

    async def handle_request(self, request: "WebRequest") -> Optional[Dict[str, Any]]:  # pyright: ignore[reportDeprecated]
        """处理请求，返回响应

        调用 callback 函数并返回其结果。支持同步和异步 callback。

        Args:
            request (WebRequest): WebRequest 请求对象

        Returns:
            dict: 包含响应信息的字典，格式为 {
                'status': int,
                'headers': dict,
                'body': bytes
            }
            或者 None 表示不处理此请求（继续原请求）
        """
        # 调用 callback 函数
        try:
            result = self._callback(request)

            # 如果 callback 是协程，等待其完成
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as ex:
            logging.warning(
                "[%s] Handle request [%s]%s failed: \n%s"
                % (
                    self.__class__.__name__,
                    request.method,
                    request.url,
                    traceback.format_exc(),
                )
            )
            return {"type": EnumProxyType.Continue}
        return result

    async def handle_response(
        self, response: "WebResponse"
    ) -> Optional[Dict[str, Any]]:  # pyright: ignore[reportDeprecated]
        """处理响应

        调用 callback 函数处理响应。支持同步和异步 callback。

        Args:
            response (WebResponse): WebResponse 响应对象

        Returns:
            dict: 包含自定义响应信息的字典，格式为 {
                'status': int,
                'headers': dict,
                'body': bytes
            }
            或者 None 表示不修改响应
        """
        try:
            result = self._callback(response)

            # 如果 callback 是协程，等待其完成
            if asyncio.iscoroutine(result):
                result = await result
        except Exception as ex:
            logging.warning(
                "[%s] Handle response for %s failed: \n%s"
                % (self.__class__.__name__, response.url, traceback.format_exc())
            )
            return None

        return result

    def get_interception_pattern(self) -> Dict[str, Any]:  # pyright: ignore[reportDeprecated]
        """获取用于 CDP Fetch.enable 的模式

        Returns:
            dict: CDP 拦截模式
        """
        pattern: Dict[str, Any] = {"urlPattern": self._url_pattern}  # pyright: ignore[reportDeprecated]
        if self._resource_type:
            pattern["resourceType"] = self._resource_type
        pattern["requestStage"] = "Request"
        return pattern


class WebRequest:
    """Web请求封装类

    封装HTTP请求的基本信息，包括URL、方法、请求头和请求体。
    用于在代理处理中传递请求信息。
    """

    def __init__(
        self, url: str, method: str, headers: Dict[str, str], body: Optional[bytes]  # pyright: ignore[reportDeprecated]
    ) -> None:
        """初始化 WebRequest

        Args:
            url (str): 请求的URL地址
            method (str): HTTP请求方法（GET、POST等）
            headers (dict): 请求头字典
            body (bytes): 请求体内容
        """
        self._url: str = url
        self._method: str = method
        self._headers: Dict[str, str] = headers  # pyright: ignore[reportDeprecated]
        self._body: bytes | None = body

    @staticmethod
    def from_request(request: Any) -> "WebRequest":  # pyright: ignore[reportUnknownParameterType]
        """从 pyppeteer 的 Request 对象创建 WebRequest 对象

        Args:
            request: pyppeteer 的 Request 对象

        Returns:
            WebRequest: 新创建的 WebRequest 对象
        """
        # 获取请求 URL
        url: str = request.url

        # 获取请求方法
        method: str = request.method if request.method else "GET"

        # 获取请求头
        headers: Dict[str, str] = dict(request.headers) if request.headers else {}  # pyright: ignore[reportDeprecated]

        # 获取请求体（POST 数据）
        body: Optional[bytes] = None  # pyright: ignore[reportDeprecated]
        if request.postData:
            if isinstance(request.postData, bytes):
                body = request.postData
            else:
                body = request.postData.encode("utf-8")

        return WebRequest(url=url, method=method, headers=headers, body=body)

    @property
    def url(self) -> str:
        """获取请求URL"""
        return self._url

    @property
    def method(self) -> str:
        """获取HTTP请求方法"""
        return self._method

    @property
    def headers(self) -> Dict[str, str]:  # pyright: ignore[reportDeprecated]
        """获取请求头字典"""
        return self._headers

    @property
    def body(self) -> Optional[bytes]:  # pyright: ignore[reportDeprecated]
        """获取请求体内容"""
        return self._body

    @property
    def content(self) -> str:
        if self._body:
            return self._body.decode("utf-8")
        return ""


class WebResponse:
    """Web请求响应类"""

    def __init__(
        self, request: WebRequest, status: int, headers: Dict[str, str], body: bytes  # pyright: ignore[reportDeprecated]
    ) -> None:
        """初始化

        Args:
            request: WebRequest 对象
            status (int): HTTP 状态码
            headers (dict): 响应头字典
            body (bytes): 响应体内容
        """
        self._request: WebRequest = request
        self._status: int = status
        self._headers: Dict[str, str] = {k.lower(): v for k, v in headers.items()}  # pyright: ignore[reportDeprecated]
        self._body: bytes = body

    @property
    def url(self) -> str:
        """获取响应对应的URL"""
        return self._request.url

    @property
    def status(self) -> int:
        """HTTP状态码"""
        return self._status

    @property
    def ok(self) -> bool:
        """判断响应是否成功

        Returns:
            bool: 如果状态码在200-299范围内返回True，否则返回False
        """
        return self._status == 0 or 200 <= self._status <= 299

    @property
    def headers(self) -> Dict[str, str]:  # pyright: ignore[reportDeprecated]
        """获取响应头字典

        所有header名称都是小写的。
        """
        return self._headers

    @property
    def body(self) -> bytes:
        return self._body

    @property
    def content(self) -> str:
        if self._body:
            return self._body.decode("utf-8")
        return ""

    @property
    def request(self) -> WebRequest:
        """获取关联的 Request 对象"""
        return self._request


class WebProxy(object):
    """Web Proxy

    使用代理规则处理网络请求拦截和响应
    """

    def __init__(self, page: pyppeteer.page.Page, proxy_rules: List[ProxyRule]) -> None:  # pyright: ignore[reportDeprecated]
        """初始化 Web 代理

        Args:
            page: pyppeteer.page.Page 实例
            proxy_rules: ProxyRule 对象列表
        """
        self._page: pyppeteer.page.Page = page
        self._client: Any = page._client
        self._proxy_rules: List[ProxyRule] = proxy_rules  # pyright: ignore[reportDeprecated]

    async def setup_interception(self) -> None:
        """启用网络请求拦截

        使用 CDP 的 Fetch 域直接拦截网络请求，不依赖 pyppeteer 的内部实现
        （pyppeteer 依赖的 ``Network.setRequestInterception`` 在 Chrome 119+
        已被移除）。
        """
        logging.info("[%s] Setup interception" % self.__class__.__name__)
        patterns: List[Dict[str, Any]] = []  # pyright: ignore[reportDeprecated]
        for rule in self._proxy_rules:
            if rule.stage == EnumProxyStage.Request:
                patterns.append(rule.get_interception_pattern())

        await asyncio.gather(
            self._client.send(
                "Network.setCacheDisabled",
                {"cacheDisabled": True},
            ),
            self._client.send(
                "Fetch.enable",
                {"patterns": patterns},
            ),
        )

        self._client.on("Fetch.requestPaused", self._on_request_paused)

    async def get_http_response(self, request: WebRequest) -> Optional[WebResponse]:  # pyright: ignore[reportDeprecated]
        """通过aiohttp获取http返回包，返回WebResponse对象

        Args:
            request (WebRequest): WebRequest 请求对象

        Returns:
            WebResponse 对象
        """
        # 获取请求 URL
        print("get_http_response", request.url)
        url = request.url

        # 获取请求头并转换为字典
        headers: Dict[str, str] = {}  # pyright: ignore[reportDeprecated]
        for key, value in request.headers.items():
            headers[key] = value

        try:
            # 使用 utils.fetch 替代 aiohttp 直接调用
            result = await utils.fetch(
                url=url,
                method=request.method,
                headers=headers,
                respond_with_headers=True,
            )
            status, response_headers, body = cast(
                Tuple[int, Dict[str, str], bytes], result  # pyright: ignore[reportDeprecated]
            )

            # 返回 WebResponse 对象
            return WebResponse(
                request=request,
                status=status,
                headers=response_headers,
                body=body,
            )
        except Exception as ex:
            logging.warning(
                "[WebProxy] Get HTTP response failed for %s: %s" % (url, str(ex))
            )
            return None

    def get_rule(
        self, request: Any  # pyright: ignore[reportAny]
    ) -> Tuple[Optional[ProxyRule], Optional[ProxyRule]]:  # pyright: ignore[reportDeprecated]
        """获取与request匹配的规则

        Args:
            request: pyppeteer 的 Request 对象

        Returns:
            tuple: (request_rule, response_rule)
        """
        request_rule: Optional[ProxyRule] = None  # pyright: ignore[reportDeprecated]
        response_rule: Optional[ProxyRule] = None  # pyright: ignore[reportDeprecated]

        # 遍历所有规则，查找匹配的规则
        for rule in self._proxy_rules:
            if rule.matches(request):
                if rule.stage == EnumProxyStage.Request and request_rule is None:
                    request_rule = rule
                elif rule.stage == EnumProxyStage.Response and response_rule is None:
                    response_rule = rule

        return request_rule, response_rule

    def _on_request_paused(self, event: Dict[str, Any]) -> None:
        """Fetch.requestPaused 事件回调，调度异步处理任务"""
        self._client._loop.create_task(self._handle_request_paused(event))

    async def _handle_request_paused(self, event: Dict[str, Any]) -> None:
        """处理被 Fetch 域拦截的请求

        遍历所有代理规则，找到第一个匹配的规则并调用其 handle 方法

        Args:
            event: Fetch.requestPaused 事件参数
        """
        request_id: Optional[str] = event.get("requestId")
        request_data: Dict[str, Any] = event.get("request") or {}
        url: str = request_data.get("url", "")
        method: str = request_data.get("method", "GET")
        raw_headers: Dict[str, str] = request_data.get("headers") or {}
        headers: Dict[str, str] = {k.lower(): v for k, v in raw_headers.items()}
        post_data: Any = request_data.get("postData")
        body: Optional[bytes] = None
        if post_data:
            if isinstance(post_data, bytes):
                body = post_data
            else:
                body = str(post_data).encode("utf-8")

        web_request = WebRequest(url=url, method=method, headers=headers, body=body)
        logging.info(
            "[%s] Handle request %s %s" % (self.__class__.__name__, method, url)
        )

        request_rule, response_rule = self.get_rule(web_request)
        if request_rule:
            result = await request_rule.handle_request(web_request)
            if not isinstance(result, dict):
                logging.warning(
                    "[WebProxy] Unexpected return type %s from callback, continuing request: %s"
                    % (type(result), url)
                )
                await self._continue_request(request_id, post_data=post_data)
                return
            proxy_type: int = result.pop("type", 0)
            if proxy_type == EnumProxyType.Mock:
                await self._fulfill_request(request_id, result)
                return
            elif proxy_type == EnumProxyType.Block:
                await self._fail_request(request_id, "Failed")
                return
            elif not response_rule:
                await self._continue_request(request_id, post_data=post_data)
                return

            response = await self.get_http_response(web_request)
            if not response:
                # 没有获取到回包
                await self._continue_request(request_id, post_data=post_data)
                return
            result = await response_rule.handle_response(response)
            if result:
                await self._fulfill_request(request_id, result)
            else:
                await self._continue_request(request_id, post_data=post_data)
            return

        # 没有匹配的 request 规则，继续原请求
        logging.info(
            "[WebProxy] No request rule matched, continue request: %s" % url
        )
        await self._continue_request(request_id, post_data=post_data)

    async def _continue_request(
        self,
        request_id: Optional[str],
        overrides: Optional[Dict[str, Any]] = None,
        post_data: Any = None,
    ) -> None:
        """继续被拦截的请求

        Fetch.continueRequest 默认不会保留请求体，必须显式传入 ``postData``，
        否则 POST 请求（如章节内容接口）会丢失请求体导致服务端拒绝。
        """
        if not request_id:
            return
        params: Dict[str, Any] = {"requestId": request_id}
        if overrides:
            params.update(overrides)
        if post_data is not None:
            # Fetch.continueRequest 的 postData 字段是 base64 编码的，
            # 必须先将原始请求体编码为 base64。
            raw = post_data.encode("utf-8") if isinstance(post_data, str) else bytes(post_data)
            params["postData"] = base64.b64encode(raw).decode("ascii")
        try:
            await self._client.send("Fetch.continueRequest", params)
        except Exception as ex:
            logging.warning("[WebProxy] Continue request failed: %s" % str(ex))

    async def _fail_request(
        self, request_id: Optional[str], error_reason: str = "Failed"
    ) -> None:
        """终止被拦截的请求"""
        if not request_id:
            return
        try:
            await self._client.send(
                "Fetch.failRequest",
                {"requestId": request_id, "errorReason": error_reason},
            )
        except Exception as ex:
            logging.warning("[WebProxy] Fail request failed: %s" % str(ex))

    async def _fulfill_request(
        self, request_id: Optional[str], response: Dict[str, Any]
    ) -> None:
        """用自定义响应满足被拦截的请求"""
        if not request_id:
            return
        status: int = response.get("status", 200)
        headers: Dict[str, str] = response.get("headers") or {}
        body: Any = response.get("body")
        params: Dict[str, Any] = {
            "requestId": request_id,
            "responseCode": status,
        }
        # 这些响应头与 Fetch.fulfillRequest 提供的完整（已解码）响应体冲突，
        # 会导致页面 load 事件无法触发。必须移除。
        strip_headers = {
            "transfer-encoding",
            "content-encoding",
            "content-length",
            "connection",
        }
        response_headers: List[Dict[str, str]] = [
            {"name": k, "value": v}
            for k, v in headers.items()
            if k.lower() not in strip_headers
        ]
        if response_headers:
            params["responseHeaders"] = response_headers
        if body is not None:
            if isinstance(body, str):
                body = body.encode("utf-8")
            params["body"] = base64.b64encode(body).decode("ascii")
        try:
            await self._client.send("Fetch.fulfillRequest", params)
        except Exception as ex:
            logging.warning("[WebProxy] Fulfill request failed: %s" % str(ex))

    def get_interception_patterns(self) -> List[Dict[str, Any]]:
        """获取所有规则的拦截模式

        Returns:
            list: CDP 拦截模式列表
        """
        patterns: List[Dict[str, Any]] = []
        for rule in self._proxy_rules:
            patterns.append(rule.get_interception_pattern())
        return patterns
