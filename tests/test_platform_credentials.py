import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from config.platform_credentials import parse_text

class CredentialParsing(unittest.TestCase):
    def test_chinese_separate_lines_with_empty_delimiter(self):
        parsed=parse_text('API密钥\nfixture-public\n密钥:\nfixture-secret','BINANCE')
        self.assertEqual(parsed.values,{'api_key':'fixture-public','secret_key':'fixture-secret'})

    def test_empty_label_must_not_consume_next_label(self):
        parsed=parse_text('API密钥:\n私钥:\nfixture-secret','BINANCE')
        self.assertNotIn('api_key',parsed.values)
        self.assertEqual(parsed.values.get('secret_key'),'fixture-secret')

    def test_repr_redacts_values(self):
        self.assertNotIn('fixture-secret',repr(parse_text('secretKey: fixture-secret','BINANCE')))

if __name__=='__main__':unittest.main()
