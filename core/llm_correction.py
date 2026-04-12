# core/llm_correction.py — 双引擎 OMR 结果 JSON 化与大模型纠错
from __future__ import annotations

import base64
import io
import json
import logging
import re
import time
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .config import JianpuNote
from .jianpu_txt_editor import (
    JianpuScoreMeta,
    JianpuTxtMeasure,
    JianpuTxtNote,
    JianpuTxtScore,
    _duration_to_underlines,
    parse_txt,
    serialize_txt,
)
from .utils import log_message

_SYSTEM_PROMPT = (
    '你是一名专业的音乐理论校对助手，擅长根据节奏、调号、旋律逻辑、和声规则，'
    '对结构化乐谱数据进行纠错。\n'
    '你不会重写整段音乐，只会在必要时对错误的音符进行最小修改。\n'
    '你必须严格遵守节奏合法性、调号规则、旋律线条合理性、声部一致性等音乐理论约束。'
)

_BASE_USER_PROMPT = (
    '下面是两个引擎的识别结果 JSON 数据。请基于这两个结果识别出节奏、音高、结构上的问题，'
    '并进行最小修改以使其成为合法且合理的音乐片段。\n'
    '只修改明显错误的音符，不要重写正确的部分。\n'
    '输出必须是一个完全合法的 JSON 对象，且只能包含以下字段：\n'
    '{\n'
    '  "corrected_score": [...],\n'
    '  "changes": [ ... ]\n'
    '}\n'
    '不要输出解释性文字、注释、Markdown、代码块或任何额外文本。\n'
    '不要在 JSON 前后添加任何字符。\n'
    '如果无法生成有效 JSON，请返回一个空 JSON 对象 {}。\n'
)

_PROVIDER_ENDPOINTS = {
    'openai': 'https://api.openai.com/v1/chat/completions',
    'qwen': 'https://api.qwen.com/v1/chat/completions',
    'deepseek': 'https://api.deepseek.com/v1/chat/completions',
    'moonshot': 'https://api.moonshot.com/v1/chat/completions',
    'doubao': 'https://ark.cn-beijing.volces.com/api/v3/chat/completions',
    'gemini': 'https://generativelanguage.googleapis.com/v1beta',
}

_PROVIDER_DEFAULT_MODEL = {
    'openai': 'gpt-3.5-turbo',
    'qwen': 'qwen_7b_chat',
    'deepseek': 'deepseek-chat',
    'moonshot': 'moonshot-1',
    'doubao': 'doubao-seed-2-0-lite-260215',
    'gemini': 'gemini-3-flash-preview',
}

_PROVIDER_MODEL_ALIASES = {
    'doubao': {
        'doubao-1': 'doubao-seed-1-6-250615',
    },
}

_PROVIDER_MODEL_CANDIDATES = {
    'gemini': ['gemini-3-flash-preview', 'gemini-2.0-flash'],
}

_PROVIDER_AUTO_ORDER = ['doubao', 'gemini']
_MAX_MEASURES_PER_CALL = 4
_MAX_RETRIES = 4
_MAX_RESPONSE_TOKENS = 1600
_MAX_REQUEST_BYTES = 120_000
_MAX_PROMPT_TOKENS = 32000
_LLM_API_TIMEOUT = 60


def _estimate_token_count(text: str) -> int:
    # Approximate GPT-style tokens by characters. This is a simple heuristic,
    # used to avoid excessively large prompts before sending them to Gemini.
    return max(1, len(text) // 4)


def _note_to_json(note: JianpuNote) -> dict[str, Any]:
    return {
        'symbol': note.symbol,
        'accidental': note.accidental,
        'upper_dots': note.upper_dots,
        'lower_dots': note.lower_dots,
        'duration': note.duration,
        'duration_dots': note.duration_dots,
        'midi': note.midi,
        'is_rest': note.is_rest,
    }


def _build_engine_payload(
    engine_name: str,
    measures: list[list[JianpuNote]],
    time_signature: str,
    tonic_name: str,
    start_measure: int = 0,
) -> dict[str, Any]:
    return {
        'engine': engine_name,
        'time_signature': time_signature,
        'tonic_name': tonic_name,
        'start_measure': start_measure,
        'measures': [
            {
                'measure': start_measure + idx,
                'notes': [_note_to_json(note) for note in measure],
            }
            for idx, measure in enumerate(measures)
        ],
    }


def _clean_json_text(text: str) -> str:
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = text.strip('`')
    text = text.replace('“', '"').replace('”', '"')
    text = text.replace('\n', ' ')
    left = text.find('{')
    right = text.rfind('}')
    if left != -1 and right != -1 and right > left:
        return text[left:right + 1]
    return text


def _repair_json_text(text: str) -> str:
    text = _clean_json_text(text)
    text = re.sub(r',\s*([}\]])', r'\1', text)
    text = re.sub(r"(?P<quote>')(?P<content>[^']*?)\1", r'"\g<content>"', text)
    return text


def _parse_llm_response(response_text: str) -> Optional[dict[str, Any]]:
    cleaned = _clean_json_text(response_text)
    try:
        return json.loads(cleaned)
    except Exception:
        repaired = _repair_json_text(response_text)
        try:
            return json.loads(repaired)
        except Exception:
            return None


def _serialize_for_json(value: Any) -> Any:
    if isinstance(value, JianpuNote):
        return _note_to_json(value)
    if isinstance(value, dict):
        return {k: _serialize_for_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_serialize_for_json(v) for v in value]
    return value


def _build_input_payload(chunk_payload: dict[str, Any]) -> dict[str, Any]:
    return _serialize_for_json({
        'version': '1.0',
        'source': 'dual' if chunk_payload.get('audiveris', {}).get('measures') else 'oemer',
        'oemer': chunk_payload.get('oemer', {}),
        'audiveris': chunk_payload.get('audiveris', {}),
    })


def _read_jianpu_txt(path: Path) -> Optional[str]:
    try:
        return path.read_text(encoding='utf-8-sig', errors='replace')
    except Exception:
        return None


def _build_prompt(
    chunk_payload: dict[str, Any],
    segment_index: int,
    total_segments: int,
) -> str:
    payload = _build_input_payload(chunk_payload)
    prompt = (
        _BASE_USER_PROMPT
        + f'这是第 {segment_index + 1}/{total_segments} 段，仅处理下面给出的谱段。'
        + '如果你认为某段已经合法，只需原样返回。\n'
        + '请确保 corrected_score 的长度与输入 measures 的长度一致。\n\n'
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )
    if _estimate_token_count(prompt) > _MAX_PROMPT_TOKENS:
        log_message(
            '[LLM] 纠错 prompt 过大，已超过令牌上限，放弃请求。',
            logging.WARNING,
        )
        return ''
    return prompt


def _build_jianpu_txt_prompt(raw_text: str) -> str:
    prompt = (
        '你是一名专业的简谱校对助手，擅长根据节奏、调号、旋律逻辑、和声规则，'
        '对 .jianpu.txt 文本进行纠错。\n'
        '请仅纠正明显错误的音符、时值或小节结构，不要无故重写正确的部分。\n'
        '输出必须是完整的 .jianpu.txt 文本，包含 [meta] 和 [score] 区块；'
        '不要输出 Markdown、代码块标记、额外说明或注释。\n'
        '如果无法直接生成有效的 .jianpu.txt 文件，请返回空字符串。\n\n'
        + raw_text.strip()
    )
    if _estimate_token_count(prompt) > _MAX_PROMPT_TOKENS:
        log_message(
            '[LLM] 简谱文本纠错 prompt 过大，已超过令牌上限，放弃请求。',
            logging.WARNING,
        )
        return ''
    return prompt


def _clean_jianpu_txt_response(text: str) -> str:
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        lines = text.splitlines()
        if lines and lines[0].startswith('```'):
            lines = lines[1:]
        if lines and lines[-1].startswith('```'):
            lines = lines[:-1]
        text = '\n'.join(lines).strip()
    return text


def _parse_llm_jianpu_txt(response_text: str) -> Optional[JianpuTxtScore]:
    text = _clean_jianpu_txt_response(response_text)
    try:
        return parse_txt(text)
    except Exception:
        try:
            return parse_txt(text.strip())
        except Exception:
            return None


def _jianpu_txt_to_jianpu_measures(score: JianpuTxtScore) -> list[list[JianpuNote]]:
    measures: list[list[JianpuNote]] = []
    for measure in score.measures:
        notes: list[JianpuNote] = []
        for txt_note in measure.notes:
            notes.append(JianpuNote(
                txt_note.symbol,
                txt_note.accidental,
                txt_note.upper_dots,
                txt_note.lower_dots,
                txt_note.quarter_length(),
                1 if txt_note.aug_dot else 0,
                None,
                txt_note.is_rest,
            ))
        measures.append(notes)
    return measures


def _build_jianpu_txt_from_measures(
    measures: list[list[JianpuNote]],
    title: str,
    key: str,
    time_signature: str,
    tempo: int = 120,
) -> str:
    score = JianpuTxtScore(
        meta=JianpuScoreMeta(title=title, composer='', key=key, time=time_signature, tempo=tempo),
    )
    for measure in measures:
        bar = JianpuTxtMeasure()
        for note in measure:
            bar.notes.append(JianpuTxtNote(
                symbol=note.symbol,
                accidental=note.accidental,
                upper_dots=note.upper_dots,
                lower_dots=note.lower_dots,
                dashes=max(0, int(note.duration) - 1) if note.duration >= 1.0 else 0,
                underlines=_duration_to_underlines(note.duration),
                aug_dot=(note.duration_dots > 0),
            ))
        score.measures.append(bar)
    return serialize_txt(score)


def _build_jianpu_txt_from_measures_to_file(
    measures: list[list[JianpuNote]],
    title: str,
    key: str,
    time_signature: str,
    path: Path,
) -> bool:
    try:
        path.write_text(
            _build_jianpu_txt_from_measures(measures, title, key, time_signature),
            encoding='utf-8',
        )
        return True
    except Exception:
        return False


def apply_llm_correction_to_jianpu_txt(
    jianpu_txt_path: Path,
    llm_api_key: str,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    cache_dir: Optional[Path] = None,
) -> Optional[tuple[JianpuTxtScore, str]]:
    if not llm_api_key or not jianpu_txt_path.exists():
        return None

    raw_text = _read_jianpu_txt(jianpu_txt_path)
    if not raw_text:
        log_message(f'[LLM] 无法读取简谱文本文件: {jianpu_txt_path}', logging.WARNING)
        return None

    prompt = _build_jianpu_txt_prompt(raw_text)
    if cache_dir is not None:
        _save_chunk_debug(cache_dir, 'llm_jianpu_txt_input.txt', raw_text)

    response = _call_chat_api(
        llm_api_key,
        llm_provider or 'auto',
        llm_model,
        prompt,
    )
    if not response:
        log_message('[LLM] 简谱文本纠错调用失败。', logging.WARNING)
        return None

    corrected_text = _clean_jianpu_txt_response(response)
    if cache_dir is not None:
        try:
            cache_dir.mkdir(parents=True, exist_ok=True)
            (cache_dir / 'llm_jianpu_txt_raw.txt').write_text(corrected_text, encoding='utf-8')
        except Exception:
            pass

    corrected_score = _parse_llm_jianpu_txt(corrected_text)
    if corrected_score is None:
        log_message('[LLM] 简谱文本纠错结果解析失败。', logging.WARNING)
        return None

    return corrected_score, corrected_text


def _get_provider_endpoint(provider: str) -> Optional[str]:
    return _PROVIDER_ENDPOINTS.get(provider)


def _get_provider_model(provider: str, model: Optional[str]) -> str:
    if model:
        normalized = str(model).strip()
        alias_map = _PROVIDER_MODEL_ALIASES.get(provider, {})
        return alias_map.get(normalized, normalized)
    return _PROVIDER_DEFAULT_MODEL.get(provider, 'gpt-3.5-turbo')


def _get_provider_model_candidates(provider: str, model: Optional[str]) -> list[str]:
    if provider == 'gemini' and not model:
        return _PROVIDER_MODEL_CANDIDATES.get('gemini', [])
    return [_get_provider_model(provider, model)]


def _build_gemini_image_content(image_path: Path) -> Optional[dict[str, Any]]:
    try:
        from PIL import Image
    except Exception:
        return None
    try:
        with Image.open(image_path) as img:
            img = img.convert('RGB')
            img.thumbnail((512, 512), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.ANTIALIAS)
            with io.BytesIO() as buffer:
                img.save(buffer, format='PNG')
                encoded = base64.b64encode(buffer.getvalue()).decode('ascii')
        return {'type': 'image', 'imageUri': f'data:image/png;base64,{encoded}'}
    except Exception:
        return None


def _build_request(
    provider: str,
    api_key: str,
    model: str,
    prompt: str,
    original_image_path: Optional[Path] = None,
) -> Optional[list[tuple[str, dict[str, str], bytes]]]:
    endpoint = _get_provider_endpoint(provider)
    if endpoint is None:
        return None
    urls: list[str] = []
    if isinstance(endpoint, list):
        for item in endpoint:
            urls.append(str(item))
    else:
        urls.append(str(endpoint))

    reqs: list[tuple[str, dict[str, str], bytes]] = []
    if provider == 'gemini':
        image_content = None
        if original_image_path is not None and original_image_path.exists():
            image_content = _build_gemini_image_content(original_image_path)
        for url_base in urls:
            for candidate_model_name in _get_provider_model_candidates(provider, model):
                url = f'{url_base}/models/{candidate_model_name}:generateContent'
                headers = {'Content-Type': 'application/json'}
                key = api_key.strip()
                auth_methods = []
                if key.startswith('AIza'):
                    auth_methods.append(('query', f'{url}?key={key}', headers.copy()))
                    auth_headers = headers.copy()
                    auth_headers['Authorization'] = f'Bearer {key}'
                    auth_methods.append(('header', url, auth_headers))
                else:
                    auth_methods.append(('header', url, {**headers, 'Authorization': f'Bearer {key}' }))

                contents: list[dict[str, Any]] = []
                user_content: dict[str, Any] = {
                    'role': 'user',
                    'parts': [
                        {'text': prompt},
                    ],
                }
                if image_content is not None:
                    user_content['parts'].insert(0, image_content)
                contents.append(user_content)

                body = {
                    'contents': contents,
                    'systemInstruction': {
                        'parts': [
                            {'text': _SYSTEM_PROMPT},
                        ],
                    },
                    'generationConfig': {
                        'temperature': 0.0,
                        'maxOutputTokens': _MAX_RESPONSE_TOKENS,
                        'candidateCount': 1,
                    },
                }
                for _, target_url, target_headers in auth_methods:
                    data = json.dumps(body, ensure_ascii=False).encode('utf-8')
                    if len(data) > _MAX_REQUEST_BYTES:
                        log_message(
                            f'[LLM] Gemini 请求体大小 {len(data)} 字节超过限制 {_MAX_REQUEST_BYTES}，放弃该请求。',
                            logging.WARNING,
                        )
                        return None
                    reqs.append((target_url, target_headers, data))
        return reqs

    if provider == 'doubao':
        body = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt},
            ],
            'max_tokens': _MAX_RESPONSE_TOKENS,
            'temperature': 0.0,
            'top_p': 0.7,
        }
    else:
        body = {
            'model': model,
            'temperature': 0.2,
            'messages': [
                {'role': 'system', 'content': _SYSTEM_PROMPT},
                {'role': 'user', 'content': prompt},
            ],
            'max_tokens': _MAX_RESPONSE_TOKENS,
        }

    for url in urls:
        data = json.dumps(body, ensure_ascii=False).encode('utf-8')
        if len(data) > _MAX_REQUEST_BYTES:
            log_message(
                f'[LLM] 请求体大小 {len(data)} 字节超过限制 {_MAX_REQUEST_BYTES}，放弃该请求。',
                logging.WARNING,
            )
            return None
        reqs.append((url, {
            'Content-Type': 'application/json',
            'Authorization': f'Bearer {api_key.strip()}',
        }, data))
    return reqs


def _call_chat_api(
    api_key: str,
    provider: str,
    model: Optional[str],
    prompt: str,
    original_image_path: Optional[Path] = None,
) -> Optional[str]:
    if not api_key or not prompt:
        return None
    if _estimate_token_count(prompt) > _MAX_PROMPT_TOKENS:
        log_message(
            '[LLM] prompt 令牌数超出上限，已停止调用。',
            logging.WARNING,
        )
        return None
    if provider == 'auto':
        for candidate in _PROVIDER_AUTO_ORDER:
            result = _call_chat_api(api_key, candidate, model, prompt, original_image_path=original_image_path)
            if result is not None:
                return result
        return None

    request_data = _build_request(
        provider,
        api_key,
        _get_provider_model(provider, model),
        prompt,
        original_image_path=original_image_path,
    )
    if not request_data:
        log_message(f'[LLM] 未知提供商: {provider}', logging.WARNING)
        return None

    last_error: Optional[str] = None
    for url, headers, data in request_data:
        for attempt in range(1, _MAX_RETRIES + 1):
            request = Request(url, data=data, headers=headers, method='POST')
            try:
                with urlopen(request, timeout=_LLM_API_TIMEOUT) as resp:
                    text = resp.read().decode('utf-8', errors='replace')
                    payload = json.loads(text)
                    if provider == 'gemini':
                        candidates = payload.get('candidates') or []
                        if not candidates:
                            last_error = '[LLM] Gemini 响应缺少 candidates'
                            continue
                        candidate = candidates[0]
                        content = candidate.get('content') if isinstance(candidate, dict) else None
                        if isinstance(content, list) and content:
                            text_parts: list[str] = []
                            for piece in content:
                                if isinstance(piece, dict):
                                    text_value = piece.get('text') or piece.get('content')
                                    if isinstance(text_value, str):
                                        text_parts.append(text_value)
                                elif isinstance(piece, str):
                                    text_parts.append(piece)
                            if text_parts:
                                return ''.join(text_parts)
                        if isinstance(content, dict):
                            parts = content.get('parts')
                            if isinstance(parts, list):
                                text_parts = []
                                for part in parts:
                                    if isinstance(part, dict):
                                        text_value = part.get('text') or part.get('content')
                                        if isinstance(text_value, str):
                                            text_parts.append(text_value)
                                if text_parts:
                                    return ''.join(text_parts)
                            text = content.get('text')
                            if isinstance(text, str) and text.strip():
                                return text
                        last_error = '[LLM] Gemini 响应格式不正确'
                        continue
                    if provider == 'doubao':
                        choices = payload.get('choices') or []
                        if not choices:
                            last_error = '[LLM] 豆包响应缺少 choices'
                            continue
                        message = choices[0].get('message') if isinstance(choices[0], dict) else None
                        if isinstance(message, dict):
                            content = message.get('content')
                            if isinstance(content, str) and content.strip():
                                return content
                            if isinstance(content, dict):
                                text = content.get('text')
                                if isinstance(text, str) and text.strip():
                                    return text
                        last_error = '[LLM] 豆包响应格式不正确'
                        continue
                    choices = payload.get('choices') or []
                    if not choices:
                        last_error = '[LLM] 响应缺少 choices'
                        continue
                    return choices[0].get('message', {}).get('content')
            except HTTPError as exc:
                message = f'[LLM] {provider} API 错误 {exc.code}: {exc.reason} ({url})'
                log_message(message, logging.WARNING)
                if exc.code in {401, 403}:
                    return None
                if exc.code == 404 and provider == 'gemini':
                    break
                last_error = message
            except URLError as exc:
                message = f'[LLM] {provider} 网络错误: {exc}'
                log_message(message, logging.WARNING)
                last_error = message
                if attempt < _MAX_RETRIES:
                    time.sleep(1)
            except Exception as exc:
                message = f'[LLM] {provider} 解析响应失败: {exc}'
                log_message(message, logging.WARNING)
                last_error = message
                if 'resp' not in locals() or resp is None:
                    continue
                try:
                    text = resp.read().decode('utf-8', errors='replace')
                    payload = json.loads(text)
                except Exception:
                    continue
                if provider == 'gemini':
                    candidates = payload.get('candidates') or []
                    if not candidates:
                        last_error = '[LLM] Gemini 响应缺少 candidates'
                        continue
                    candidate = candidates[0]
                    content = candidate.get('content') if isinstance(candidate, dict) else None
                    if isinstance(content, list) and content:
                        text_parts = []
                        for piece in content:
                            if isinstance(piece, dict):
                                text_value = piece.get('text') or piece.get('content')
                                if isinstance(text_value, str):
                                    text_parts.append(text_value)
                            elif isinstance(piece, str):
                                text_parts.append(piece)
                        if text_parts:
                            return ''.join(text_parts)
                    last_error = '[LLM] Gemini 响应格式不正确'
                    continue
                if provider == 'doubao':
                    choices = payload.get('choices') or []
                    if not choices:
                        last_error = '[LLM] 豆包响应缺少 choices'
                        continue
                    message = choices[0].get('message') if isinstance(choices[0], dict) else None
                    if isinstance(message, dict):
                        content = message.get('content')
                        if isinstance(content, str) and content.strip():
                            return content
                        if isinstance(content, dict):
                            text = content.get('text')
                            if isinstance(text, str) and text.strip():
                                return text
                    last_error = '[LLM] 豆包响应格式不正确'
                    continue
                choices = payload.get('choices') or []
                if not choices:
                    last_error = '[LLM] 响应缺少 choices'
                    continue
                return choices[0].get('message', {}).get('content')
    if last_error:
        log_message(last_error, logging.WARNING)
    return None


def _validate_note_data(note_data: dict[str, Any], original: Optional[JianpuNote] = None) -> Optional[JianpuNote]:
    try:
        midi_value = note_data.get('midi')
        if midi_value is None and original is not None:
            midi_value = original.midi
        return JianpuNote(
            symbol=str(note_data.get('symbol', original.symbol if original else '0')),
            accidental=str(note_data.get('accidental', original.accidental if original else '')),
            upper_dots=int(note_data.get('upper_dots', original.upper_dots if original else 0) or 0),
            lower_dots=int(note_data.get('lower_dots', original.lower_dots if original else 0) or 0),
            duration=float(note_data.get('duration', original.duration if original else 1.0) or 1.0),
            duration_dots=int(note_data.get('duration_dots', original.duration_dots if original else 0) or 0),
            midi=(None if midi_value is None else int(midi_value)),
            is_rest=bool(note_data.get('is_rest', original.is_rest if original else False)),
        )
    except Exception:
        return None


def _validate_corrected_score(
    corrected_score: Any,
    original_measures: list[list[JianpuNote]],
) -> Optional[list[list[JianpuNote]]]:
    if not isinstance(corrected_score, list):
        return None
    if len(corrected_score) != len(original_measures):
        return None
    validated: list[list[JianpuNote]] = []
    total_changes = 0
    total_notes = 0
    for idx, measure in enumerate(corrected_score):
        if not isinstance(measure, list):
            return None
        original_notes = original_measures[idx]
        if len(measure) != len(original_notes):
            return None
        measure_notes: list[JianpuNote] = []
        for note_idx, note_data in enumerate(measure):
            if not isinstance(note_data, dict):
                return None
            validated_note = _validate_note_data(note_data, original_notes[note_idx])
            if validated_note is None:
                return None
            if validated_note != original_notes[note_idx]:
                total_changes += 1
            total_notes += 1
            measure_notes.append(validated_note)
        validated.append(measure_notes)
    if total_notes > 0 and total_changes / total_notes > 0.5:
        log_message('[LLM] 纠错结果修改过多，已拒绝不可信输出。', logging.WARNING)
        return None
    return validated


def _split_chunks(
    measures_oemer: list[list[JianpuNote]],
    measures_audiveris: list[list[JianpuNote]],
    time_sig_oemer: str,
    tonic_oemer: str,
    time_sig_audiveris: str,
    tonic_audiveris: str,
    chunk_size: int = _MAX_MEASURES_PER_CALL,
) -> list[dict[str, Any]]:
    max_length = max(len(measures_oemer), len(measures_audiveris))
    chunks: list[dict[str, Any]] = []
    for start in range(0, max_length, chunk_size):
        chunk_o = measures_oemer[start:start + chunk_size]
        chunk_a = measures_audiveris[start:start + chunk_size]
        chunks.append({
            'oemer': _build_engine_payload('oemer', chunk_o, time_sig_oemer, tonic_oemer, start),
            'audiveris': _build_engine_payload('audiveris', chunk_a, time_sig_audiveris, tonic_audiveris, start),
            'start': start,
            'original_measures': _merge_chunk_measures(chunk_o, chunk_a),
        })
    return chunks


def _merge_chunk_measures(
    chunk_o: list[list[JianpuNote]],
    chunk_a: list[list[JianpuNote]],
) -> list[list[JianpuNote]]:
    length = max(len(chunk_o), len(chunk_a))
    merged: list[list[JianpuNote]] = []
    for idx in range(length):
        if idx < len(chunk_o):
            merged.append(chunk_o[idx])
        elif idx < len(chunk_a):
            merged.append(chunk_a[idx])
        else:
            merged.append([])
    return merged


def _merge_corrected_segments(
    segments: list[dict[str, Any]],
    total_length: int,
) -> Optional[list[list[JianpuNote]]]:
    corrected: list[list[JianpuNote]] = []
    for segment in sorted(segments, key=lambda s: s['start']):
        corrected.extend(segment['corrected_measures'])
    if len(corrected) != total_length:
        return None
    return corrected


def _save_chunk_debug(cache_dir: Optional[Path], name: str, data: Any) -> None:
    if cache_dir is None:
        return
    try:
        cache_dir.mkdir(parents=True, exist_ok=True)
        serializable = _serialize_for_json(data)
        (cache_dir / name).write_text(json.dumps(serializable, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        pass


def apply_llm_correction_to_dual_results(
    measures_oemer: list[list[JianpuNote]],
    time_sig_oemer: str,
    tonic_oemer: str,
    measures_audiveris: list[list[JianpuNote]],
    time_sig_audiveris: str,
    tonic_audiveris: str,
    llm_api_key: str,
    cache_dir: Optional[Path] = None,
    llm_provider: Optional[str] = None,
    llm_model: Optional[str] = None,
    original_image_path: Optional[Path] = None,
) -> Optional[tuple[list[list[JianpuNote]], str, str]]:
    if not llm_api_key:
        return None
    provider = llm_provider or 'auto'
    if provider == 'auto':
        log_message('[LLM] 使用自动 LLM 服务选择。')

    payload_chunks = _split_chunks(
        measures_oemer,
        measures_audiveris,
        time_sig_oemer,
        tonic_oemer,
        time_sig_audiveris,
        tonic_audiveris,
    )
    total_measures = max(len(measures_oemer), len(measures_audiveris))
    chunks_results: list[dict[str, Any]] = []

    for segment_index, chunk in enumerate(payload_chunks):
        chunk_prompt = _build_prompt(chunk, segment_index, len(payload_chunks))
        _save_chunk_debug(cache_dir, f'llm_chunk_{segment_index + 1}_input.json', chunk)
        response = _call_chat_api(
            llm_api_key,
            provider,
            llm_model,
            chunk_prompt,
            original_image_path=original_image_path,
        )
        if not response:
            log_message(f'[LLM] 第 {segment_index + 1} 段调用失败，放弃 LLM 纠错。', logging.WARNING)
            return None

        _save_chunk_debug(cache_dir, f'llm_chunk_{segment_index + 1}_raw.txt', response)
        corrected_json = _parse_llm_response(response)
        if corrected_json is None:
            log_message(f'[LLM] 第 {segment_index + 1} 段返回的 JSON 解析失败。', logging.WARNING)
            return None

        corrected_score = corrected_json.get('corrected_score')
        if corrected_score is None:
            log_message(f'[LLM] 第 {segment_index + 1} 段缺少 corrected_score 字段。', logging.WARNING)
            return None

        original_measures = chunk['original_measures']
        validated = _validate_corrected_score(corrected_score, original_measures)
        if validated is None:
            log_message(f'[LLM] 第 {segment_index + 1} 段纠错结果无效或修改过多。', logging.WARNING)
            return None

        chunks_results.append({
            'start': chunk['start'],
            'corrected_measures': validated,
            'changes': corrected_json.get('changes', []),
        })
        _save_chunk_debug(cache_dir, f'llm_chunk_{segment_index + 1}_parsed.json', corrected_json)

    merged = _merge_corrected_segments(chunks_results, total_measures)
    if merged is None:
        log_message('[LLM] 纠错段合并失败，放弃 LLM 纠错。', logging.WARNING)
        return None

    if cache_dir is not None:
        _save_chunk_debug(cache_dir, 'llm_merged_corrected_score.json', [
            [_note_to_json(note) for note in measure]
            for measure in merged
        ])

    return merged, time_sig_oemer, tonic_oemer
