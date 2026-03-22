import unittest

from notion_markdown_converter import markdown_to_notion_blocks


class NotionMarkdownConverterTests(unittest.TestCase):
    def test_common_review_markdown_is_converted(self) -> None:
        source = (
            "2. **动词选择**：\n\n"
            "- 原句：`I was affected`\n"
            "- 建议：`I was impacted` 或 `my position was eliminated`（后者更具体委婉）。\n"
        )

        blocks = markdown_to_notion_blocks(source)
        block_types = [block["type"] for block in blocks]
        self.assertIn("numbered_list_item", block_types)
        self.assertIn("bulleted_list_item", block_types)

        rich_text_entries = []
        for block in blocks:
            payload = block[block["type"]]
            rich_text_entries.extend(payload.get("rich_text", []))

        bold_hits = [
            item
            for item in rich_text_entries
            if item.get("text", {}).get("content") == "动词选择"
            and item.get("annotations", {}).get("bold") is True
        ]
        self.assertTrue(bold_hits, "Expected bold annotation for 动词选择")

        code_hits = [
            item.get("text", {}).get("content")
            for item in rich_text_entries
            if item.get("annotations", {}).get("code") is True
        ]
        self.assertIn("I was affected", code_hits)
        self.assertIn("I was impacted", code_hits)


if __name__ == "__main__":
    unittest.main()
