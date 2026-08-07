import logging
import asyncio
import json
import os
import re
from typing import Awaitable, Callable, Optional

from openai import OpenAI

from llm_sanitize import strip_llm_artifacts

logger = logging.getLogger(__name__)


class Translator:
    """文本翻译器；支持环境变量或请求内传入的 API Key / Base URL（与 Summarizer 一致）。"""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None,
    ):
        self.client = None
        self._translation_model = model or os.getenv("OPENAI_TRANSLATION_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o"
        self._timeout = float(os.getenv("OPENAI_TRANSLATION_TIMEOUT", "120"))

        self.language_map = {
            "zh": "中文（简体）",
            "zh-tw": "中文（繁体）",
            "en": "English",
            "ja": "日本語",
            "ko": "한국어",
            "fr": "Français",
            "de": "Deutsch",
            "es": "Español",
            "it": "Italiano",
            "pt": "Português",
            "ru": "Русский",
            "ar": "العربية",
            "hi": "हिन्दी",
            "vi": "Tiếng Việt",
        }

        eff_key = (api_key.strip() if isinstance(api_key, str) and api_key.strip() else None) or os.getenv(
            "OPENAI_API_KEY"
        )
        if isinstance(api_key, str) and api_key.strip():
            eff_base = (base_url or "").strip().rstrip("/") or os.getenv(
                "OPENAI_BASE_URL", "https://api.openai.com/v1"
            )
        else:
            eff_base = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")

        if not eff_key:
            logger.warning("未设置可用的 OpenAI API Key，翻译将不可用")
            return

        try:
            self.client = OpenAI(api_key=eff_key, base_url=eff_base, timeout=self._timeout)
            logger.info("Translator OpenAI 客户端初始化成功")
        except Exception as e:
            logger.error(f"初始化 OpenAI 客户端失败: {e}")
            self.client = None
    
    def _detect_source_language(self, text: str) -> str:
        """检测源文本语言"""
        # 简单的语言检测逻辑
        if "**检测语言:**" in text:
            lines = text.split('\n')
            for line in lines:
                if "**检测语言:**" in line:
                    lang = line.split(":")[-1].strip()
                    return lang
        
        # 基于字符统计的简单检测
        total_chars = len(text)
        if total_chars == 0:
            return "en"
        
        # 统计中文字符
        chinese_chars = len(re.findall(r'[\u4e00-\u9fff]', text))
        chinese_ratio = chinese_chars / total_chars
        
        # 统计日文字符
        japanese_chars = len(re.findall(r'[\u3040-\u309f\u30a0-\u30ff]', text))
        japanese_ratio = japanese_chars / total_chars
        
        # 统计韩文字符
        korean_chars = len(re.findall(r'[\uac00-\ud7af]', text))
        korean_ratio = korean_chars / total_chars
        
        if chinese_ratio > 0.1:
            return "zh"
        elif japanese_ratio > 0.05:
            return "ja"
        elif korean_ratio > 0.05:
            return "ko"
        else:
            return "en"

    def _normalize_lang_code(self, code: str) -> str:
        if not code:
            return ""
        c = str(code).lower().strip().strip("*").strip()
        if c.startswith("zh"):
            return "zh"
        if len(c) >= 2 and c[:2] in self.language_map:
            return c[:2]
        return c

    def normalize_lang_code(self, code: Optional[str]) -> str:
        """对外统一语言代码，与 should_translate 内部一致。"""
        return self._normalize_lang_code(code or "")

    def infer_language_code(self, text: str) -> str:
        """从正文推断语言代码（ISO 风格），供转录元信息缺失时使用。"""
        return self._detect_source_language(text or "")

    def should_translate(self, source_language: str, target_language: str) -> bool:
        """判断是否需要翻译"""
        if not source_language or not target_language:
            return False

        source_lang = self._normalize_lang_code(source_language)
        target_lang = self._normalize_lang_code(target_language)

        if source_lang == target_lang:
            return False

        chinese_variants = ["zh", "zh-cn", "zh-hans", "chinese"]
        if source_lang in chinese_variants and target_lang in chinese_variants:
            return False

        return True

    def languages_differ_for_translation(self, source_code: Optional[str], summary_lang: Optional[str]) -> bool:
        """摘要语言（用户选择）与源语言不同时为 True，用于是否生成/展示翻译。"""
        s = self.normalize_lang_code(source_code or "")
        t = self.normalize_lang_code(summary_lang or "")
        return bool(s and t and self.should_translate(s, t))

    def _smart_chunk_text(self, text: str, max_chars_per_chunk: int = 4000) -> list:
        """智能分块文本用于翻译"""
        chunks = []

        # 首先按段落分割
        paragraphs = [p for p in text.split('\n\n') if p.strip()]
        current_chunk = ""

        for paragraph in paragraphs:
            # 如果当前段落加上现有块超过限制
            if len(current_chunk) + len(paragraph) + 2 > max_chars_per_chunk and current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = paragraph
            else:
                if current_chunk:
                    current_chunk += "\n\n" + paragraph
                else:
                    current_chunk = paragraph

        # 添加最后一块
        if current_chunk.strip():
            chunks.append(current_chunk.strip())

        # 如果某个块仍然太长，按句子进一步分割
        final_chunks = []
        for chunk in chunks:
            if len(chunk) <= max_chars_per_chunk:
                final_chunks.append(chunk)
            else:
                # 按句子分割
                sentences = re.split(r'[.!?。！？]\s+', chunk)
                current_sub_chunk = ""

                for sentence in sentences:
                    if len(current_sub_chunk) + len(sentence) + 2 > max_chars_per_chunk and current_sub_chunk:
                        final_chunks.append(current_sub_chunk.strip())
                        current_sub_chunk = sentence
                    else:
                        if current_sub_chunk:
                            current_sub_chunk += ". " + sentence
                        else:
                            current_sub_chunk = sentence

                if current_sub_chunk.strip():
                    final_chunks.append(current_sub_chunk.strip())

        return final_chunks

    def _format_name_glossary(self, name_glossary: Optional[list[dict]]) -> str:
        if not name_glossary:
            return ""
        lines = []
        for item in name_glossary:
            if not isinstance(item, dict):
                continue
            source = str(item.get("source_name") or "").strip()
            target = str(item.get("target_name") or item.get("romanized_name") or "").strip()
            if source and target:
                lines.append(f"- {source} => {target}")
        return "\n".join(lines)

    def _format_speaker_profile(self, speaker_profile: Optional[dict]) -> str:
        if not isinstance(speaker_profile, dict) or not speaker_profile:
            return ""

        lines = []
        relationship = str(speaker_profile.get("relationship") or "").strip()
        pronoun_policy = str(speaker_profile.get("pronoun_policy") or "").strip()
        if relationship:
            lines.append(f"- Relationship/context: {relationship}")
        if pronoun_policy:
            lines.append(f"- Pronoun policy: {pronoun_policy}")

        speakers = speaker_profile.get("speakers")
        if isinstance(speakers, list):
            for item in speakers[:12]:
                if not isinstance(item, dict):
                    continue
                label = str(item.get("label") or item.get("speaker") or "").strip()
                gender = str(item.get("gender") or "").strip()
                role = str(item.get("role") or "").strip()
                pronouns = str(item.get("vietnamese_pronouns") or item.get("pronouns") or "").strip()
                evidence = str(item.get("evidence") or "").strip()
                parts = [p for p in [label, gender, role, pronouns] if p]
                if parts:
                    line = "- " + " | ".join(parts)
                    if evidence:
                        line += f" | evidence: {evidence[:160]}"
                    lines.append(line)

        return "\n".join(lines)

    async def build_speaker_profile(
        self,
        text: str,
        target_language: str,
        source_language: Optional[str] = None,
        name_glossary: Optional[list[dict]] = None,
    ) -> dict:
        """Infer speaker gender/relationship context before translation."""
        if not self.client or not text:
            return {}

        target_n = self._normalize_lang_code(target_language)
        if target_n != "vi":
            return {}

        source_lang_name = self.language_map.get(
            self._normalize_lang_code(source_language or self._detect_source_language(text)),
            source_language or "source language",
        )
        glossary_text = self._format_name_glossary(name_glossary)
        sample = text[:14000]
        system_prompt = """You infer speaker identity, gender, relationship, and Vietnamese pronoun policy from a transcript.

Rules:
- Return valid JSON only.
- Do not invent exact names if the transcript does not support them.
- If gender is uncertain, use "unknown" and give a conservative pronoun policy.
- Preserve speaker perspective: first person stays first person, second person stays second person.
- For Vietnamese translation, choose pronouns that are stable across the whole conversation.
- Prefer natural Vietnamese dialogue pronouns such as tôi/bạn when uncertain, and anh/em/chị/cô/chú only when context supports it."""
        user_prompt = f"""Source language: {source_lang_name}
Target language: Vietnamese

Name glossary:
{glossary_text or "(none)"}

Transcript:
{sample}

Return JSON with this shape:
{{
  "relationship": "brief context between speakers",
  "pronoun_policy": "how Vietnamese pronouns should be used consistently",
  "speakers": [
    {{
      "label": "speaker or character label",
      "gender": "male|female|unknown",
      "role": "role/relationship if known",
      "vietnamese_pronouns": "pronouns to use for this speaker/addressing others",
      "evidence": "short source evidence"
    }}
  ]
}}"""
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self._translation_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1200,
                temperature=0.0,
            )
            raw = strip_llm_artifacts(response.choices[0].message.content or "").strip()
            match = re.search(r"\{[\s\S]*\}", raw)
            parsed = json.loads(match.group(0) if match else raw)
            if not isinstance(parsed, dict):
                return {}
            speakers = parsed.get("speakers")
            if not isinstance(speakers, list):
                parsed["speakers"] = []
            return {
                "relationship": str(parsed.get("relationship") or "").strip(),
                "pronoun_policy": str(parsed.get("pronoun_policy") or "").strip(),
                "speakers": [
                    item for item in parsed.get("speakers", [])
                    if isinstance(item, dict)
                ][:12],
            }
        except Exception as e:
            logger.warning(f"生成说话人/称谓画像失败: {e}")
            return {}

    async def build_name_glossary(
        self,
        text: str,
        target_language: str,
        source_language: Optional[str] = None,
    ) -> list[dict]:
        """Build a stable glossary for recurring Chinese person names."""
        if not self.client or not text:
            return []
        src_n = self._normalize_lang_code(source_language or self._detect_source_language(text))
        if src_n != "zh":
            return []

        target_lang_name = self.language_map.get(self._normalize_lang_code(target_language), target_language)
        sample = text[:12000]
        system_prompt = f"""You extract Chinese person-name glossary entries for translation into {target_lang_name}.

Rules:
- Return valid JSON only.
- Return an array of objects with: source_name, target_name, romanized_name, notes.
- source_name must be the exact Chinese name from the source text.
- target_name should be natural pinyin romanization without tone marks, e.g. 河黎 -> He Li, 成江 -> Cheng Jiang.
- Do not create Vietnamese tone spellings like "Hè Lê" for Chinese names.
- Include only likely person/character names that appear in the text.
- Keep the list concise and stable."""
        user_prompt = f"""Extract a name glossary from this transcript:

{sample}

Return JSON only."""
        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self._translation_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                max_tokens=1200,
                temperature=0.0,
            )
            raw = strip_llm_artifacts(response.choices[0].message.content or "").strip()
            match = re.search(r"\[[\s\S]*\]", raw)
            parsed = json.loads(match.group(0) if match else raw)
            if not isinstance(parsed, list):
                return []
            glossary = []
            seen = set()
            for item in parsed:
                if not isinstance(item, dict):
                    continue
                source = str(item.get("source_name") or "").strip()
                target = str(item.get("target_name") or item.get("romanized_name") or "").strip()
                romanized = str(item.get("romanized_name") or target).strip()
                if not source or not target or source in seen:
                    continue
                if not re.search(r"[\u4e00-\u9fff]", source):
                    continue
                seen.add(source)
                glossary.append({
                    "source_name": source,
                    "target_name": target,
                    "romanized_name": romanized,
                    "notes": str(item.get("notes") or "").strip(),
                })
            return glossary[:40]
        except Exception as e:
            logger.warning(f"生成姓名术语表失败: {e}")
            return []

    async def translate_text(
        self,
        text: str,
        target_language: str,
        source_language: Optional[str] = None,
        name_glossary: Optional[list[dict]] = None,
        speaker_profile: Optional[dict] = None,
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> str:
        """
        翻译文本到目标语言
        
        Args:
            text: 要翻译的文本
            target_language: 目标语言代码
            source_language: 源语言代码（可选，会自动检测）
            
        Returns:
            翻译后的文本
        """
        try:
            if not self.client:
                logger.warning("OpenAI API不可用，无法翻译")
                return text
            
            # 检测源语言
            if not source_language:
                source_language = self._detect_source_language(text)
            
            # 如果源语言和目标语言相同，直接返回
            src_n = self._normalize_lang_code(source_language or "")
            tgt_n = self._normalize_lang_code(target_language)
            if src_n and tgt_n and src_n == tgt_n:
                return text
            
            source_lang_name = self.language_map.get(src_n, self.language_map.get(source_language, source_language))
            target_lang_name = self.language_map.get(tgt_n, self.language_map.get(target_language, target_language))
            
            logger.info(f"开始翻译：{source_lang_name} -> {target_lang_name}")
            
            # 估算文本长度，决定是否需要分块
            if len(text) > 3000:
                logger.info(f"文本较长({len(text)} chars)，启用分块翻译")
                return await self._translate_with_chunks(
                    text,
                    target_lang_name,
                    source_lang_name,
                    name_glossary,
                    speaker_profile,
                    progress_callback,
                )
            else:
                return await self._translate_single_text(text, target_lang_name, source_lang_name, name_glossary, speaker_profile)
                
        except Exception as e:
            logger.error(f"翻译失败: {str(e)}")
            return text
    
    async def _translate_single_text(
        self,
        text: str,
        target_lang_name: str,
        source_lang_name: str,
        name_glossary: Optional[list[dict]] = None,
        speaker_profile: Optional[dict] = None,
    ) -> str:
        """翻译单个文本块"""
        glossary_text = self._format_name_glossary(name_glossary)
        speaker_text = self._format_speaker_profile(speaker_profile)
        glossary_rules = ""
        if glossary_text:
            glossary_rules = f"""

姓名术语表（必须严格使用）：
{glossary_text}

姓名规则：
- 遇到术语表中的中文姓名，必须使用右侧译名。
- 对其他中文人名，优先使用自然拼音罗马化，不要翻成带越南声调的近似音。"""
        speaker_rules = ""
        if speaker_text:
            speaker_rules = f"""

说话人/称谓画像（必须遵守，尤其翻译成越南语时）：
{speaker_text}

称谓规则：
- 保持说话视角，不要把“我/你/他/她”的方向翻反。
- 越南语称谓必须前后一致；不要在 anh/em/chị/tôi/bạn 之间随机切换。
- 如果性别或关系不确定，优先使用中性、稳妥的“tôi/bạn”或省略称谓，不要强行猜测。"""
        system_prompt = f"""你是专业翻译专家。请将{source_lang_name}文本准确翻译为{target_lang_name}。

翻译要求：
- 保持原文的格式和结构（包括段落分隔、标题等）
- 准确传达原意，语言自然流畅
- 保留专业术语的准确性
- 不要添加解释或注释
- 如果遇到Markdown格式，请保持格式不变
- 只输出译文正文：不要前言、尾注、客套话，不要写「如需调整请告诉我」等任何元话语。{glossary_rules}{speaker_rules}"""

        user_prompt = f"""请将以下{source_lang_name}文本翻译为{target_lang_name}：

{text}

只返回翻译结果，不要添加任何说明。"""

        try:
            response = await asyncio.to_thread(
                self.client.chat.completions.create,
                model=self._translation_model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=4000,
                temperature=0.1
            )

            return strip_llm_artifacts(response.choices[0].message.content or "")
        except Exception as e:
            logger.error(f"单文本翻译失败: {e}")
            return text
    
    async def _translate_with_chunks(
        self,
        text: str,
        target_lang_name: str,
        source_lang_name: str,
        name_glossary: Optional[list[dict]] = None,
        speaker_profile: Optional[dict] = None,
        progress_callback: Optional[Callable[[int, int], Awaitable[None]]] = None,
    ) -> str:
        """分块翻译长文本"""
        chunks = self._smart_chunk_text(text, max_chars_per_chunk=4000)
        logger.info(f"分割为 {len(chunks)} 个块进行翻译")
        
        translated_chunks = []
        glossary_text = self._format_name_glossary(name_glossary)
        speaker_text = self._format_speaker_profile(speaker_profile)
        glossary_rules = ""
        if glossary_text:
            glossary_rules = f"""

姓名术语表（必须严格使用）：
{glossary_text}

姓名规则：
- 遇到术语表中的中文姓名，必须使用右侧译名。
- 对其他中文人名，优先使用自然拼音罗马化，不要翻成带越南声调的近似音。"""
        speaker_rules = ""
        if speaker_text:
            speaker_rules = f"""

说话人/称谓画像（必须遵守，尤其翻译成越南语时）：
{speaker_text}

称谓规则：
- 保持说话视角，不要把“我/你/他/她”的方向翻反。
- 越南语称谓必须前后一致；不要在 anh/em/chị/tôi/bạn 之间随机切换。
- 如果性别或关系不确定，优先使用中性、稳妥的“tôi/bạn”或省略称谓，不要强行猜测。"""
        
        for i, chunk in enumerate(chunks):
            logger.info(f"正在翻译第 {i+1}/{len(chunks)} 块...")
            if progress_callback:
                await progress_callback(i + 1, len(chunks))
            
            system_prompt = f"""你是专业翻译专家。请将{source_lang_name}文本准确翻译为{target_lang_name}。

这是完整文档的第{i+1}部分，共{len(chunks)}部分。

翻译要求：
- 保持原文的格式和结构
- 准确传达原意，语言自然流畅
- 保留专业术语的准确性
- 不要添加解释或注释
- 保持与前后文的连贯性
- 只输出译文正文，不要尾注或元话语。{glossary_rules}{speaker_rules}"""

            user_prompt = f"""请将以下{source_lang_name}文本翻译为{target_lang_name}：

{chunk}

只返回翻译结果。"""

            try:
                response = await asyncio.to_thread(
                    self.client.chat.completions.create,
                    model=self._translation_model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt}
                    ],
                    max_tokens=4000,
                    temperature=0.1
                )

                translated_chunk = response.choices[0].message.content or ""
                translated_chunks.append(strip_llm_artifacts(translated_chunk))
            except Exception as e:
                logger.error(f"翻译第 {i+1} 块失败: {e}")
                # 失败时保留原文
                translated_chunks.append(chunk)
        
        # 合并翻译结果
        return strip_llm_artifacts("\n\n".join(translated_chunks))
