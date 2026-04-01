# util/llm_client.py

from __future__ import annotations

import os
import logging
from typing import Optional, Type
from pathlib import Path
from datetime import datetime
import json
import uuid
from openai import OpenAI
from typing import List, Dict, Any
from pydantic import BaseModel

from util.llm_tracker import get_tracker

# 전역 클라이언트 (프로세스 내에서 재사용)
_client: Optional[OpenAI] = None
_client_conf: tuple[str, str | None] | None = None

logger = logging.getLogger(__name__)

def _read_int_env(*keys: str) -> int | None:
    for k in keys:
        raw = (os.getenv(k) or "").strip()
        if raw == "":
            continue
        try:
            return int(float(raw))
        except Exception:
            continue
    return None


def _default_max_tokens() -> int | None:
    """
    Default max_tokens for Chat Completions.

    vLLM/OpenAI-compatible servers may keep generating until EOS if max_tokens is
    not set. Many local models/tool-calling modes can fail to emit EOS quickly,
    appearing to "hang" while the server keeps generating.

    Env overrides:
      - FINAGENT_LLM_MAX_TOKENS, LLM_MAX_TOKENS, OPENAI_MAX_TOKENS
    """
    mt = _read_int_env("FINAGENT_LLM_MAX_TOKENS", "LLM_MAX_TOKENS", "OPENAI_MAX_TOKENS")
    if mt is None:
        return 2048
    if mt <= 0:
        return None
    return mt

def _trace_max_chars() -> int | None:
    """
    Max chars for prompt/response persisted to trace logs.
    If None, store full text (no truncation).
    """
    raw = os.getenv("FINAGENT_LLM_TRACE_MAX_CHARS", "").strip()
    if not raw:
        return None
    try:
        if raw.lower() in {"none", "null", "unlimited"}:
            return None
        n = int(raw)
        if n <= 0:
            return None
        # Keep user-configured truncation bounded to avoid accidental disk blowups.
        return max(1_000, min(n, 50_000_000))
    except Exception:
        return None


def _truncate_text(text: str | None, max_chars: int | None) -> tuple[str | None, dict[str, Any]]:
    if text is None:
        return None, {"present": False}
    if max_chars is None:
        return text, {"present": True, "truncated": False, "len": len(text)}
    if len(text) <= max_chars:
        return text, {"present": True, "truncated": False, "len": len(text)}
    truncated = text[:max_chars] + f"\n...[TRUNCATED {len(text) - max_chars} chars]"
    return truncated, {"present": True, "truncated": True, "len": len(text), "kept": max_chars}


def _append_jsonl(dir_path: Path, filename: str, record: dict[str, Any]) -> None:
    try:
        dir_path.mkdir(parents=True, exist_ok=True)
        path = dir_path / filename
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str))
            f.write("\n")
    except Exception as e:
        logger.warning(f"[LLM Trace] Failed to write jsonl: {e}")


def _get_client() -> OpenAI:
    """
    Create (or reuse) an OpenAI client.

    Supports OpenAI-compatible endpoints (vLLM/Ollama/LM Studio/hosted providers) via BASE_URL.

    Env (preferred → fallback):
      - API key:    FINAGENT_OPENAI_API_KEY, OPENAI_API_KEY
      - Base URL:   FINAGENT_OPENAI_BASE_URL, OPENAI_BASE_URL

    Notes:
      - Some local servers ignore API keys; if BASE_URL is set and no key is provided, a dummy key is used.
      - If env changes across runs, the client is re-created.
    """
    global _client, _client_conf

    base_url = (os.getenv("FINAGENT_OPENAI_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "").strip()
    base_url = base_url or None

    api_key = (os.getenv("FINAGENT_OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
    if not api_key:
        if base_url:
            api_key = "EMPTY"
        else:
            raise RuntimeError(
                "OPENAI_API_KEY 환경변수가 설정되어 있지 않습니다. "
                "(또는 FINAGENT_OPENAI_API_KEY 사용 가능)"
            )

    conf = (api_key, base_url)
    if _client is None or _client_conf != conf:
        _client_conf = conf
        kwargs: dict[str, Any] = {"api_key": api_key}
        if base_url is not None:
            kwargs["base_url"] = base_url
        _client = OpenAI(**kwargs)

    return _client


def call_llm(
    model: str,
    system_prompt: str,
    user_prompt: str,
    tools: List[Dict[str, Any]] = None,
    tool_map: Dict[str, Any] = None,  # 실제 실행할 함수들의 맵 {"func_name": func_obj}
    max_turns: int = 5,
    temperature: float = 0.3,
    response_format: Optional[Type[BaseModel]] = None,
    target_tool_name: str = None,
    react_log_path: Optional[Path] = None,  # ReAct 로그 저장 경로
    react_agent_name: str = "agent",  # ReAct 에이전트 이름 (로그 파일명)
    context: Optional[str] = None,  # LLM 호출 컨텍스트 (usage tracking용)
) -> Any:
    """
    Tool Calling Loop (ReAct Pattern) 구현체.
    
    1. LLM 호출 (tools 포함)
    2. tool_calls 있으면 실행 후 결과 메시지 추가 -> 루프 계속
    3. tool_calls 없으면 최종 답변 반환 -> 루프 종료
    
    Args:
        tool_map: 도구 이름과 실제 실행 가능한 파이썬 함수(Callable)의 매핑
        target_tool_name: 이 도구가 호출되면 함수 실행 없이 인자(dict)를 즉시 반환함 (Structured Output 대용)
        react_log_path: ReAct 로그를 저장할 디렉토리 경로 (None이면 저장 안함)
        react_agent_name: 로그 파일명에 사용할 에이전트 이름
    """
    client = _get_client()
    max_tokens = _default_max_tokens()

    call_id = uuid.uuid4().hex
    started_at = datetime.now().isoformat()
    max_chars = _trace_max_chars()

    system_prompt_logged, system_prompt_meta = _truncate_text(system_prompt, max_chars=max_chars)
    user_prompt_logged, user_prompt_meta = _truncate_text(user_prompt, max_chars=max_chars)

    tools_logged: Any = None
    tools_meta: dict[str, Any] = {"present": tools is not None}
    if tools is not None:
        try:
            # tools schema can be large but is still useful for debugging.
            tools_logged = tools
            tools_meta["count"] = len(tools) if isinstance(tools, list) else None
        except Exception:
            tools_logged = None

    def _emit_trace(event: dict[str, Any]) -> None:
        if react_log_path is None:
            return
        base = {
            "call_id": call_id,
            "started_at": started_at,
            "ended_at": datetime.now().isoformat(),
            "agent_name": react_agent_name,
            "context": context,
            "model": model,
            "temperature": temperature,
            "max_turns": max_turns,
            "target_tool_name": target_tool_name,
            "system_prompt": system_prompt_logged,
            "user_prompt": user_prompt_logged,
            "system_prompt_meta": system_prompt_meta,
            "user_prompt_meta": user_prompt_meta,
            "tools": tools_logged,
            "tools_meta": tools_meta,
        }
        base.update(event)
        _append_jsonl(Path(react_log_path), "llm_calls.jsonl", base)
    
    # ReAct 로그 수집
    react_log = {
        "agent_name": react_agent_name,
        "model": model,
        "started_at": datetime.now().isoformat(),
        "max_turns": max_turns,
        "tools_available": list(tool_map.keys()) if tool_map else [],
        "target_tool": target_tool_name,
        "turns": [],
    }

    # Persist prompts into react_log so the per-call JSON has full I/O context.
    react_log["system_prompt"] = system_prompt_logged
    react_log["user_prompt"] = user_prompt_logged
    react_log["system_prompt_meta"] = system_prompt_meta
    react_log["user_prompt_meta"] = user_prompt_meta
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]

    if tools is None:
        # If a structured response model is provided, use the beta parser.
        if response_format is not None:
            resp = client.beta.chat.completions.parse(
                model=model,
                messages=messages,
                response_format=response_format,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            # Track usage
            if hasattr(resp, 'usage') and resp.usage:
                get_tracker().track_call(
                    model=model,
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    context=context,
                )
            parsed = resp.choices[0].message.parsed
            try:
                parsed_json = parsed.model_dump(mode="json") if hasattr(parsed, "model_dump") else parsed
            except Exception:
                parsed_json = str(parsed)
            _emit_trace({
                "mode": "parse",
                "status": "completed",
                "usage": {
                    "prompt_tokens": getattr(resp.usage, "prompt_tokens", None) if getattr(resp, "usage", None) else None,
                    "completion_tokens": getattr(resp.usage, "completion_tokens", None) if getattr(resp, "usage", None) else None,
                    "total_tokens": getattr(resp.usage, "total_tokens", None) if getattr(resp, "usage", None) else None,
                },
                "response": parsed_json,
            })
            return parsed

        # Otherwise fall back to normal chat completion.
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        # Track usage
        if hasattr(resp, 'usage') and resp.usage:
            get_tracker().track_call(
                model=model,
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                context=context,
            )
        msg = resp.choices[0].message
        content = msg.content or ""
        content_logged, content_meta = _truncate_text(content, max_chars=max_chars)
        _emit_trace({
            "mode": "text",
            "status": "completed",
            "usage": {
                "prompt_tokens": getattr(resp.usage, "prompt_tokens", None) if getattr(resp, "usage", None) else None,
                "completion_tokens": getattr(resp.usage, "completion_tokens", None) if getattr(resp, "usage", None) else None,
                "total_tokens": getattr(resp.usage, "total_tokens", None) if getattr(resp, "usage", None) else None,
            },
            "response": content_logged,
            "response_meta": content_meta,
        })
        return content

    # ReAct 로깅
    if tool_map:
        logger.info("=" * 60)
        logger.info("[ReAct] Starting Tool Calling Loop")
        logger.info(f"  Max turns: {max_turns}")
        logger.info(f"  Available tools: {list(tool_map.keys())}")
        if target_tool_name:
            logger.info(f"  Target tool: {target_tool_name}")
        logger.info("=" * 60)

    def _save_react_log(final_output: Any = None, status: str = "completed"):
        """ReAct 로그를 파일에 저장"""
        if react_log_path is None:
            return
        log_file: Path | None = None
        try:
            react_log["ended_at"] = datetime.now().isoformat()
            react_log["status"] = status
            react_log["total_turns"] = len(react_log["turns"])
            if final_output is not None:
                react_log["final_output"] = str(final_output)
            
            # 디렉토리 생성
            log_dir = Path(react_log_path)
            log_dir.mkdir(parents=True, exist_ok=True)
            
            # 파일명 생성
            timestamp = datetime.now().strftime("%H%M%S")
            log_file = log_dir / f"react_{react_agent_name}_{timestamp}.json"
            
            with open(log_file, "w", encoding="utf-8") as f:
                json.dump(react_log, f, indent=2, ensure_ascii=False)
            
            logger.info(f"[ReAct] Log saved to: {log_file}")
        except Exception as e:
            logger.warning(f"[ReAct] Failed to save log: {e}")
        finally:
            # Also emit a compact JSONL record so all calls can be grepped easily.
            try:
                _emit_trace({
                    "mode": "tools",
                    "status": status,
                    "react_log_file": str(log_file) if log_file else None,
                    "final_output": str(final_output) if final_output is not None else None,
                })
            except Exception:
                pass

    for turn in range(max_turns):
        turn_log = {
            "turn": turn + 1,
            "timestamp": datetime.now().isoformat(),
            "tool_calls": [],
        }
        
        # 1. LLM 호출
        tool_choice = None
        # If we expect a single structured output via a specific tool, force the model to call it.
        # Do NOT force tool_choice when tool_map is provided (ReAct mode needs multiple tools before the final tool).
        if target_tool_name and not tool_map:
            tool_choice = {"type": "function", "function": {"name": target_tool_name}}

        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
            tool_choice=tool_choice,
            temperature=temperature,
            max_tokens=max_tokens,
        )

        # Track usage
        if hasattr(resp, 'usage') and resp.usage:
            get_tracker().track_call(
                model=model,
                prompt_tokens=resp.usage.prompt_tokens,
                completion_tokens=resp.usage.completion_tokens,
                context=context,
            )
            # Best-effort: attach per-turn usage into the react log.
            try:
                turn_log["usage"] = {
                    "prompt_tokens": resp.usage.prompt_tokens,
                    "completion_tokens": resp.usage.completion_tokens,
                    "total_tokens": getattr(resp.usage, "total_tokens", None),
                }
            except Exception:
                pass

        msg = resp.choices[0].message
        messages.append(msg)
        
        # LLM 응답 로깅 (content가 있으면)
        if msg.content:
            turn_log["llm_reasoning"] = msg.content
        
        # 2. Tool 호출 여부 확인
        if msg.tool_calls:
            if tool_map:
                logger.info("-" * 50)
                logger.info(f"[ReAct Turn {turn + 1}/{max_turns}] LLM requesting {len(msg.tool_calls)} tool call(s)")

            for tc in msg.tool_calls:
                func_name = tc.function.name
                args_str = tc.function.arguments
                
                tool_call_log = {
                    "tool_name": func_name,
                    "arguments": args_str,
                }
                
                # Target Tool이면 즉시 반환
                if target_tool_name and func_name == target_tool_name:
                    if tool_map:
                        logger.info(f"  ✓ [Final] {target_tool_name} called → Returning structured output")
                    tool_call_log["status"] = "final_output"
                    turn_log["tool_calls"].append(tool_call_log)
                    react_log["turns"].append(turn_log)
                    
                    try:
                        result = json.loads(args_str)
                        _save_react_log(result, "completed")
                        return result
                    except json.JSONDecodeError:
                        _save_react_log(None, "error")
                        return {"error": "Failed to parse JSON arguments"}

                # 실행할 함수 찾기
                if tool_map:
                    func = tool_map.get(func_name)
                    if not func:
                        result_str = f"Error: Tool '{func_name}' not found."
                        logger.warning(f"  ✗ [{func_name}] Tool not found")
                        tool_call_log["status"] = "not_found"
                        tool_call_log["result"] = result_str
                    else:
                        try:
                            args = json.loads(args_str)
                            # 로그: 도구 호출
                            args_preview = str(args)[:100] + ("..." if len(str(args)) > 100 else "")
                            logger.info(f"  → [{func_name}] Args: {args_preview}")
                            
                            # 함수 실행
                            result = func(**args)
                            result_str = str(result) # 결과는 문자열로 변환
                            
                            # 로그: 도구 결과
                            result_preview = result_str[:200] + ("..." if len(result_str) > 200 else "")
                            logger.info(f"    ← Result: {result_preview}")
                            
                            tool_call_log["status"] = "success"
                            tool_call_log["result"] = result_str
                        except Exception as e:
                            result_str = f"Error executing '{func_name}': {str(e)}"
                            logger.error(f"  ✗ [{func_name}] Error: {str(e)}")
                            tool_call_log["status"] = "error"
                            tool_call_log["error"] = str(e)
                else:
                    result_str = f"Error: No tool_map provided for '{func_name}'"
                    tool_call_log["status"] = "no_tool_map"
                
                turn_log["tool_calls"].append(tool_call_log)
                
                # 3. 결과 메시지 추가
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": func_name,
                    "content": result_str,
                })
            
            react_log["turns"].append(turn_log)
            # 루프 계속 (다음 턴에서 결과를 보고 LLM이 다시 생각함)
            continue
            
        else:
            # 4. Tool 호출 없으면 최종 답변
            if tool_map:
                logger.info(f"[ReAct Turn {turn + 1}] LLM returned final text response")
            turn_log["final_response"] = msg.content or ""
            react_log["turns"].append(turn_log)
            _save_react_log(msg.content, "completed")
            return msg.content or ""
    
    logger.warning(f"[ReAct] Max turns ({max_turns}) reached without final answer")
    _save_react_log(None, "max_turns_reached")
    return "Error: Max turns reached without final answer."
