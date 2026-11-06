import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.acceptance_budget import assess_minimum, MicroBudget

class MicroBudgetTest(unittest.TestCase):
    def test_minimum_over_cent_is_blocked(self):
        r=assess_minimum({'minSz':'0.01','lotSz':'0.01','ctVal':'1','ctType':'linear','settleCcy':'USDT'},'100','0.0005')
        self.assertFalse(r['within_budget'])
        self.assertEqual(r['notional_usdt'],'1.00')
    def test_minimum_under_cent_includes_costs(self):
        r=assess_minimum({'minSz':'0.001','lotSz':'0.001','ctVal':'1','ctType':'linear','settleCcy':'USDT'},'1','0.0005')
        self.assertTrue(r['within_budget'])
        self.assertGreater(float(r['reserved_usdt']),.001)
    def test_no_leverage_or_cumulative_budget_expansion(self):
        b=MicroBudget();b.reserve('0.006')
        with self.assertRaises(ValueError):b.reserve('0.005')
        self.assertEqual(str(b.committed),'0.006')
        with self.assertRaises(ValueError):b.reserve('NaN')
    def test_no_static_metadata_guess_or_inverse_formula(self):
        with self.assertRaises(ValueError):assess_minimum({'minSz':'1'},'100','.001')
        row={'minSz':'1','lotSz':'1','ctVal':'1','ctType':'inverse','settleCcy':'BTC'}
        with self.assertRaises(ValueError):assess_minimum(row,'100','.001')

if __name__=='__main__':unittest.main()
