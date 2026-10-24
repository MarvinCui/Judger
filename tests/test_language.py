"""Keep shipped source, prompts, and demo fixtures in English."""
import re
import unittest
from pathlib import Path


class LanguageTests(unittest.TestCase):
    def test_source_prompts_and_demo_contain_no_cjk_text(self):
        root = Path(__file__).resolve().parents[1]
        pattern = re.compile(r'[\u3400-\u9fff]')
        for folder in ('trigger_agent', 'tests', 'examples'):
            for path in (root / folder).rglob('*'):
                if path.is_file() and path.suffix in {'.py', '.md', '.html', '.cjs'}:
                    with self.subTest(path=str(path.relative_to(root))):
                        self.assertIsNone(pattern.search(path.read_text(encoding='utf-8')))


if __name__ == '__main__':
    unittest.main()
