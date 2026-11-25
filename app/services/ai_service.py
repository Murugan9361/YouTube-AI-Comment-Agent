"""AI Comment Generation Service supporting Gemini, OpenAI, and Ollama."""

import os
from typing import Any, Dict, Optional
import httpx


class AIService:
    """Generates natural, context-aware YouTube comments using Gemini, OpenAI, or Ollama."""

    def _get_provider(self) -> str:
        return os.getenv("AI_PROVIDER", "gemini").strip().lower()

    def _build_prompt(self, instructions: Optional[str] = None, video_info: Optional[Dict[str, Any]] = None) -> tuple[str, str]:
        """
        Builds the system and user prompts.
        Strictly follows instructions:
        - Never pretend video was analyzed if video info is absent.
        - Ground in video context only when supplied.
        - Avoid promotional spam, repetitive phrases, and hashtags.
        """
        system_prompt = (
            "You are a helpful assistant crafting authentic, thoughtful, and natural comments for YouTube videos.\n"
            "Guidelines:\n"
            "1. Write in a conversational, human tone.\n"
            "2. Do NOT write promotional spam, generic praise ('Great video!'), or self-advertising ('Check out my channel!').\n"
            "3. Do NOT include hashtags unless explicitly asked.\n"
            "4. Keep the comment concise (1 to 3 sentences).\n"
            "5. If video context is provided, ground your comment strictly in that context without inventing details.\n"
            "6. If video context is NOT provided, adhere strictly to the user's instructions without fabricating facts.\n"
            "7. Output ONLY the comment text itself. Do not include surrounding quotation marks, explanations, or greetings."
        )

        user_content_parts = []
        if video_info and video_info.get("title"):
            title = video_info.get("title", "").strip()
            desc = video_info.get("description", "").strip()
            channel = video_info.get("channel_title", "").strip()
            context_str = f"Video Title: {title}"
            if channel:
                context_str += f"\nChannel: {channel}"
            if desc:
                context_str += f"\nDescription Snippet: {desc[:300]}"
            user_content_parts.append(f"Context from the YouTube video:\n{context_str}")

        if instructions and instructions.strip():
            user_content_parts.append(f"User instructions / desired tone: {instructions.strip()}")
        else:
            user_content_parts.append("Generate a positive, engaging comment relevant to this topic.")

        user_prompt = "\n\n".join(user_content_parts)
        return system_prompt, user_prompt

    async def generate_comment(
        self,
        instructions: Optional[str] = None,
        video_info: Optional[Dict[str, Any]] = None
    ) -> Dict[str, Any]:
        """Routes comment generation to the configured provider."""
        provider = self._get_provider()
        system_prompt, user_prompt = self._build_prompt(instructions, video_info)

        if provider == "gemini":
            comment = await self._generate_gemini(system_prompt, user_prompt)
        elif provider == "openai":
            comment = await self._generate_openai(system_prompt, user_prompt)
        elif provider == "ollama":
            comment = await self._generate_ollama(system_prompt, user_prompt)
        else:
            raise ValueError(
                f"Unsupported AI_PROVIDER '{provider}'. Supported providers are: 'gemini', 'openai', 'ollama'."
            )

        # Post-process to remove unwanted quotes if any
        clean_comment = comment.strip()
        if clean_comment.startswith('"') and clean_comment.endswith('"'):
            clean_comment = clean_comment[1:-1].strip()
        elif clean_comment.startswith("'") and clean_comment.endswith("'"):
            clean_comment = clean_comment[1:-1].strip()

        return {
            "status": "success",
            "provider": provider,
            "comment": clean_comment,
        }

    async def _generate_gemini(self, system_prompt: str, user_prompt: str) -> str:
        """Generates comment using Google Gemini API."""
        api_key = os.getenv("GEMINI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "Gemini API key is not configured in .env. Please set GEMINI_API_KEY or switch AI_PROVIDER."
            )

        # Use gemini-1.5-flash as default efficient model, or allow GEMINI_MODEL override
        model = os.getenv("GEMINI_MODEL", "gemini-1.5-flash").strip()
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

        payload = {
            "systemInstruction": {
                "parts": [{"text": system_prompt}]
            },
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": user_prompt}]
                }
            ],
            "generationConfig": {
                "temperature": 0.7,
                "maxOutputTokens": 300,
            }
        }

        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code != 200:
                err_msg = resp.text
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message") or err_msg
                except Exception:
                    pass
                raise RuntimeError(f"Gemini API Error ({resp.status_code}): {err_msg}")

            data = resp.json()
            candidates = data.get("candidates", [])
            if not candidates:
                raise RuntimeError("Gemini API returned an empty response.")

            parts = candidates[0].get("content", {}).get("parts", [])
            if not parts:
                raise RuntimeError("Gemini API response did not contain text parts.")

            return parts[0].get("text", "").strip()

    async def _generate_openai(self, system_prompt: str, user_prompt: str) -> str:
        """Generates comment using OpenAI API."""
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            raise ValueError(
                "OpenAI API key is not configured in .env. Please set OPENAI_API_KEY or switch AI_PROVIDER."
            )

        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip()
        url = "https://api.openai.com/v1/chat/completions"

        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.7,
            "max_tokens": 300,
        }

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.post(url, json=payload, headers=headers)
            if resp.status_code != 200:
                err_msg = resp.text
                try:
                    err_json = resp.json()
                    err_msg = err_json.get("error", {}).get("message") or err_msg
                except Exception:
                    pass
                raise RuntimeError(f"OpenAI API Error ({resp.status_code}): {err_msg}")

            data = resp.json()
            choices = data.get("choices", [])
            if not choices:
                raise RuntimeError("OpenAI API returned an empty response.")

            return choices[0].get("message", {}).get("content", "").strip()

    async def _generate_ollama(self, system_prompt: str, user_prompt: str) -> str:
        """Generates comment using local Ollama instance."""
        base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434").strip().rstrip("/")
        model = os.getenv("OLLAMA_MODEL", "llama3").strip()
        if not model:
            model = "llama3"

        url = f"{base_url}/api/chat"
        payload = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code != 200:
                    raise RuntimeError(f"Ollama returned status code {resp.status_code}: {resp.text}")

                data = resp.json()
                content = data.get("message", {}).get("content", "").strip()
                if not content:
                    raise RuntimeError("Ollama returned an empty response.")
                return content
        except httpx.ConnectError:
            raise RuntimeError(
                f"Could not connect to Ollama at {base_url}. Please ensure Ollama is installed and running."
            )


# Global singleton service instance
ai_service = AIService()
