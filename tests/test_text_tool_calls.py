import unittest

from agent.llm import ModelResponse, recover_text_tool_call

TOOLS = [{"function": {"name": "load_skill"}}, {"function": {"name": "write_file"}}]


class TextToolCallTest(unittest.TestCase):
    def test_json_object_with_known_tool_becomes_a_tool_call(self):
        response = recover_text_tool_call(ModelResponse('{"name": "load_skill", "arguments": {"name": "sprint-report"}}'), TOOLS)
        self.assertTrue(response.recovered_from_text)
        self.assertEqual(response.tool_calls[0].name, "load_skill")
        self.assertEqual(response.tool_calls[0].arguments, '{"name": "sprint-report"}')

    def test_tool_call_tags_and_code_fences_are_supported(self):
        tagged = '<tool_call>{"name": "write_file", "arguments": {"path": "a", "content": "x"}}</tool_call>'
        self.assertEqual(recover_text_tool_call(ModelResponse(tagged), TOOLS).tool_calls[0].name, "write_file")
        fenced = "```json\n" + '{"name": "load_skill", "arguments": {"name": "x"}}' + "\n```"
        self.assertEqual(len(recover_text_tool_call(ModelResponse(fenced), TOOLS).tool_calls), 1)

    def test_plain_text_and_unknown_tools_stay_text(self):
        for text in ("Готово, файл создан", '{"name": "rm", "arguments": {}}', '{"answer": 42}'):
            response = recover_text_tool_call(ModelResponse(text), TOOLS)
            self.assertEqual(response.tool_calls, [])
            self.assertEqual(response.content, text)


if __name__ == "__main__":
    unittest.main()
