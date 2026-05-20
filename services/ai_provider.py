"""
AI模型调用封装
第一版：DeepSeek 主模型调用 + JSON 输出解析 + 失败重试 + 成本限制
"""
import os
import json
import asyncio
from typing import Any, Dict, List, Optional, Union

from dotenv import load_dotenv
from openai import AsyncOpenAI


class AIProvider:
    """AI模型调用封装"""

    def __init__(self):
        load_dotenv()

        self.provider = os.getenv("AI_PROVIDER", "deepseek").strip().lower()
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-v4-flash").strip()
        self.base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com").strip()
        self.max_output_tokens = int(os.getenv("AI_MAX_OUTPUT_TOKENS", "3000"))
        self.max_calls_per_agent_run = int(os.getenv("AI_MAX_CALLS_PER_AGENT_RUN", "5"))
        self.openai_fallback_enabled = os.getenv("OPENAI_FALLBACK_ENABLED", "false").strip().lower() == "true"

        self.api_key = self._load_api_key()
        self.client = self._get_client()
        self.call_count = 0

    def _load_api_key(self) -> str:
        """读取 API Key"""
        if self.provider == "deepseek":
            api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("缺少 DEEPSEEK_API_KEY，请在项目根目录 .env 文件中配置")
            return api_key

        if self.provider == "openai":
            api_key = os.getenv("OPENAI_API_KEY", "").strip()
            if not api_key:
                raise RuntimeError("缺少 OPENAI_API_KEY，请在项目根目录 .env 文件中配置")
            return api_key

        raise RuntimeError(f"不支持的 AI_PROVIDER: {self.provider}")

    def _get_client(self) -> AsyncOpenAI:
        """创建模型客户端"""
        if self.provider == "deepseek":
            return AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=60.0
            )

        if self.provider == "openai":
            return AsyncOpenAI(
                api_key=self.api_key,
                timeout=60.0
            )

        raise RuntimeError(f"不支持的 AI_PROVIDER: {self.provider}")

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None
    ) -> Union[Dict[str, Any], List[Any]]:
        """调用模型并返回 JSON"""
        max_tokens = max_tokens or self.max_output_tokens

        async def do_call():
            self._check_cost_limits()
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format={"type": "json_object"}
            )
            content = response.choices[0].message.content or ""
            return self._safe_json_loads(content)

        return await self._retry_call(do_call)

    async def chat_text(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.2,
        max_tokens: Optional[int] = None
    ) -> str:
        """调用模型并返回文本"""
        max_tokens = max_tokens or self.max_output_tokens

        async def do_call():
            self._check_cost_limits()
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=temperature,
                max_tokens=max_tokens
            )
            return response.choices[0].message.content or ""

        return await self._retry_call(do_call)

    def _safe_json_loads(self, content: str) -> Union[Dict[str, Any], List[Any]]:
        """安全解析模型返回的 JSON"""
        raw_text = (content or "").strip()
        if not raw_text:
            raise ValueError("模型返回内容为空，无法解析 JSON")

        if raw_text.startswith("```json"):
            raw_text = raw_text[7:].strip()
        elif raw_text.startswith("```"):
            raw_text = raw_text[3:].strip()

        if raw_text.endswith("```"):
            raw_text = raw_text[:-3].strip()

        try:
            return json.loads(raw_text)
        except json.JSONDecodeError:
            pass

        decoder = json.JSONDecoder()
        for index, char in enumerate(raw_text):
            if char not in "[{":
                continue
            try:
                result, _ = decoder.raw_decode(raw_text[index:])
                return result
            except json.JSONDecodeError:
                continue

        raise ValueError(f"模型返回内容不是合法 JSON: {raw_text[:500]}")

    def _check_cost_limits(self):
        """检查单次 Agent 任务的 AI 调用次数限制"""
        if self.call_count >= self.max_calls_per_agent_run:
            raise RuntimeError(
                f"AI 调用次数已达到上限：{self.max_calls_per_agent_run} 次"
            )

        self.call_count += 1

    async def _retry_call(self, call_func, max_retries: int = 2):
        """失败重试"""
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                return await call_func()
            except Exception as e:
                last_error = e
                if attempt >= max_retries:
                    break
                await asyncio.sleep(1 + attempt)

        raise RuntimeError(f"AI 调用失败: {last_error}")

    async def test_connection(self) -> Dict[str, Any]:
        """测试 DeepSeek 连接"""
        try:
            result = await self.chat_json([
                {
                    "role": "system",
                    "content": "你是一个连接测试助手，只返回 JSON。"
                },
                {
                    "role": "user",
                    "content": "请返回 {\"ok\": true, \"message\": \"connection_success\"}"
                }
            ])

            return {
                "status": "success",
                "provider": self.provider,
                "model": self.model,
                "base_url": self.base_url if self.provider == "deepseek" else "",
                "result": result
            }

        except Exception as e:
            return {
                "status": "error",
                "provider": self.provider,
                "model": self.model,
                "base_url": self.base_url if self.provider == "deepseek" else "",
                "message": str(e)
            }