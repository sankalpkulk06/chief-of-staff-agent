"""Native tool-calling: providers must normalize Groq/Gemini wire shapes to ToolChatResult."""
from app.providers.gemini_chat import GeminiChatProvider
from app.providers.groq_chat import GroqChatProvider
from app.providers.tool_types import Tool, extract_one, supports_tools


class _Resp:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status
        self.text = ""

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self._payload = payload
        self.last_json = None

    def post(self, url, json=None, headers=None, timeout=None):
        self.last_json = json
        return _Resp(self._payload)


TOOL = Tool(name="log_meal", description="log a meal",
            parameters={"type": "object",
                        "properties": {"description": {"type": "string"}},
                        "required": ["description"]})


def test_groq_tool_call_normalized():
    payload = {"choices": [{"message": {
        "content": None,
        "tool_calls": [{"id": "c1", "type": "function",
                        "function": {"name": "log_meal", "arguments": '{"description": "a banana"}'}}],
    }}]}
    p = GroqChatProvider(api_key="k", model="m", session=_Session(payload))
    res = p.chat_tools([{"role": "user", "content": "log a banana"}], [TOOL], tool_choice="required")
    assert res.content is None                       # null content must not crash
    assert len(res.tool_calls) == 1
    tc = res.tool_calls[0]
    assert tc.name == "log_meal" and tc.arguments == {"description": "a banana"} and tc.id == "c1"
    # tools + tool_choice made it into the request
    assert p._request  # sanity
    sess = p._session
    assert sess.last_json["tool_choice"] == "required"
    assert sess.last_json["tools"][0]["function"]["name"] == "log_meal"


def test_gemini_tool_call_normalized_and_type_upcased():
    payload = {"candidates": [{"content": {"parts": [
        {"functionCall": {"name": "log_meal", "args": {"description": "a banana"}}},
    ]}}]}
    sess = _Session(payload)
    p = GeminiChatProvider(api_key="k", model="m", session=sess)
    res = p.chat_tools([{"role": "user", "content": "log a banana"}], [TOOL], tool_choice="required")
    assert res.content is None
    assert res.tool_calls[0].name == "log_meal"
    assert res.tool_calls[0].arguments == {"description": "a banana"}   # args already a dict
    # schema type upcased for Gemini, functionCallingConfig=ANY for "required"
    decl = sess.last_json["tools"][0]["functionDeclarations"][0]
    assert decl["parameters"]["type"] == "OBJECT"
    assert decl["parameters"]["properties"]["description"]["type"] == "STRING"
    assert sess.last_json["toolConfig"]["functionCallingConfig"]["mode"] == "ANY"


def test_gemini_mixed_text_and_call():
    payload = {"candidates": [{"content": {"parts": [
        {"text": "Sure!"},
        {"functionCall": {"name": "log_meal", "args": {"description": "toast"}}},
    ]}}]}
    p = GeminiChatProvider(api_key="k", model="m", session=_Session(payload))
    res = p.chat_tools([{"role": "user", "content": "x"}], [TOOL])
    assert res.content == "Sure!" and res.tool_calls[0].arguments == {"description": "toast"}


def test_estimate_calories_via_tool_and_followup():
    from app.core.calorie_service import estimate_calories
    from app.providers.tool_types import ToolCall, ToolChatResult

    class _Ready:
        def chat_tools(self, messages, tools, tool_choice="auto"):
            return ToolChatResult(tool_calls=[ToolCall(name="record_meal", arguments={
                "dish": "whey shake", "calories": 120,
                "items": [{"name": "whey", "calories": 120}],
                "protein_g": 24, "carbs_g": 3, "fat_g": 2})])
        def chat(self, messages=None):
            raise AssertionError("should use tool path")

    est = estimate_calories(_Ready(), "1 scoop whey", force=True)
    assert est["status"] == "ready" and est["calories"] == 120 and est["dish"] == "whey shake"
    assert est["protein_g"] == 24 and est["items"][0]["name"] == "whey"

    class _Ask:
        def chat_tools(self, messages, tools, tool_choice="auto"):
            return ToolChatResult(tool_calls=[ToolCall(name="ask_followup",
                                                       arguments={"question": "how much cheese?"})])
        def chat(self, messages=None):
            raise AssertionError("should use tool path")

    q = estimate_calories(_Ask(), "a quesadilla", force=False)
    assert q["status"] == "need_info" and "cheese" in q["question"]


def test_supports_tools_and_extract_one_fallback():
    groq = GroqChatProvider(api_key="k", model="m", session=_Session({"choices": []}))
    assert supports_tools(groq) is True

    class _NoTools:
        def chat(self, messages): return "x"
    assert supports_tools(_NoTools()) is False

    # extract_one falls back when tool-calling is disabled
    called = {"n": 0}
    def fb():
        called["n"] += 1
        return {"query": "fallback"}
    out = extract_one(_NoTools(), [], TOOL, fb, enabled=True)
    assert out == {"query": "fallback"} and called["n"] == 1
