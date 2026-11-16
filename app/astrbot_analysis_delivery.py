"""Only accepted QQ background analysis defers its initial model receipt."""

from __future__ import annotations

from app.astrbot_qq_persona import is_qq
from app.astrbot_silence import NO_REPLY_MARKER

_DEFERRED="qqchat_analysis_deferred_job"
_CONTROL="qqchat_analysis_keep_control_reply"


def defer_analysis_reply(event,job_id):
    if is_qq(event):
        event.set_extra(_DEFERRED,str(job_id))


def keep_control_reply(event):
    event.set_extra(_CONTROL,True)


def is_deferred_analysis_reply(event):
    return bool(is_qq(event) and event.get_extra(_DEFERRED,False)
                and not event.get_extra(_CONTROL,False))


def defer_model_receipt(event,response):
    if not is_deferred_analysis_reply(event):
        return False
    if any(getattr(response,key,None) for key in ("tools_call_name","tools_call_args","tools_call_ids")):
        return False
    # A nonempty internal marker avoids the runner retrying an empty completion.
    # The final decoration hook removes it; the background job sends its own final result.
    response.completion_text=NO_REPLY_MARKER
    if hasattr(response,"reasoning_content"):
        response.reasoning_content=None
    return True


def suppress_model_receipt(event):
    if not is_deferred_analysis_reply(event):
        return False
    result=event.get_result()
    if result is None or not result.is_llm_result():
        return False
    event.clear_result()
    event.should_call_llm(False)
    event.stop_event()
    return True
