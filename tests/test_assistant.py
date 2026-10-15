"""The assistant's tool loop, cost and fallback handling, against a fake SDK (no network, no key)."""
import types
import unittest
from unittest import mock

from mintcare import assistant


def block(type_, **kw):
    return types.SimpleNamespace(type=type_, **kw)


def message(content, stop_reason, model=assistant.MODEL, inp=1000, out=500):
    usage = types.SimpleNamespace(input_tokens=inp, output_tokens=out, cache_read_input_tokens=0,
                                  cache_creation_input_tokens=None)
    return types.SimpleNamespace(content=content, stop_reason=stop_reason, model=model, usage=usage)


class FakeStream:
    def __init__(self, final):
        self.final = final
        self.text_stream = [b.text for b in final.content if b.type == "text"]

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def get_final_message(self):
        return self.final


def fake_sdk(replies, sent):
    class Err(Exception):
        pass

    class Client:
        def __init__(self, **kw):
            self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=self.stream))

        def stream(self, **kw):
            sent.append(kw)
            return FakeStream(replies.pop(0))
    return types.SimpleNamespace(Anthropic=Client, AuthenticationError=Err, PermissionDeniedError=Err,
                                 RateLimitError=Err, APIStatusError=Err, APIConnectionError=Err)


class Assistant(unittest.TestCase):
    def run_reply(self, replies, allow=True):
        sent = []
        with mock.patch.object(assistant, "_load_sdk", return_value=fake_sdk(replies, sent)), \
             mock.patch.object(assistant, "get_key", return_value="sk-ant-test"), \
             mock.patch.object(assistant, "_private", side_effect=lambda t: t):
            res = assistant.stream_reply([{"role": "user", "content": "Is BBR on?"}], lambda t: None,
                                         allow_checks=allow)
        return res, sent

    def test_runs_a_check_then_answers(self):
        call = block("tool_use", id="tu1", name="run_check", input={"check_id": "bbr"})
        res, sent = self.run_reply([message([block("thinking", thinking=""), call], "tool_use"),
                                    message([block("text", text="Yes, BBR is on.")], "end_turn")])
        self.assertIsNone(res["error"])
        self.assertEqual(res["text"].strip(), "Yes, BBR is on.")
        self.assertEqual([m["role"] for m in res["messages"]], ["assistant", "user", "assistant"])
        result = res["messages"][1]["content"][0]
        self.assertEqual(result["tool_use_id"], "tu1")
        self.assertIn("TCP BBR", result["content"])
        self.assertNotIn("is_error", result)
        # the second request replays the first turn unchanged, thinking block included, with the same tools
        self.assertIs(sent[1]["messages"][1]["content"][0], res["messages"][0]["content"][0])
        self.assertEqual(sent[0]["tools"], sent[1]["tools"])
        self.assertEqual(sent[0]["thinking"], {"type": "adaptive"})
        self.assertAlmostEqual(res["cost"], 2 * (1000 * 4 + 500 * 20) / 1e6)

    def test_checks_not_allowed_or_bad_input(self):
        content, err = assistant.run_tool("run_check", {"check_id": "bbr"}, allowed=False)
        self.assertTrue(err)
        content, err = assistant.run_tool("run_check", {"check_id": "rm -rf /"}, allowed=True)
        self.assertTrue(err)
        self.assertIn("INVALID_INPUT", content)
        content, err = assistant.run_tool("run_check", "not a dict", allowed=True)
        self.assertTrue(err)

    def test_truncated_tool_call_is_not_run(self):
        call = block("tool_use", id="tu1", name="run_check", input={"check_id": "bb"})
        with mock.patch.object(assistant, "run_tool") as rt:
            res, _sent = self.run_reply([message([call], "max_tokens")])
        rt.assert_not_called()
        self.assertTrue(res["error"])

    def test_fallback_echo_drops_the_declined_part(self):
        content = [block("thinking", thinking=""), block("text", text="Partial "),
                   block("tool_use", id="x", name="list_checks", input={}),
                   block("fallback"), block("text", text="rest")]
        kept = assistant.echo_blocks(content)
        self.assertEqual([b.type for b in kept], ["text", "fallback", "text"])

    def test_unpriced_model_is_marked_approximate(self):
        res, _sent = self.run_reply([message([block("text", text="hi")], "end_turn", model="other-model")])
        self.assertTrue(res["approx"])
