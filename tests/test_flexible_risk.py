import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from core.isolated_risk_ledger import IsolatedRiskLedger

class FlexibleRisk(unittest.TestCase):
    def position(self):
        book=IsolatedRiskLedger()
        book.register_entry('SOXL-USDT-SWAP','buy',1,100,10,10)
        book.configure('SOXL-USDT-SWAP',stop_loss_enabled=False,breakeven_enabled=False,trailing_enabled=False)
        return book

    def test_zero_and_large_stop_thresholds(self):
        book=self.position()
        for threshold in (0,1,10,100):
            book.configure('SOXL-USDT-SWAP',stop_loss_pct=threshold)
        book.configure('SOXL-USDT-SWAP',stop_loss_enabled=True,stop_loss_pct=0)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',100)[0],'TRIGGER_STOP_LOSS')

    def test_amount_stop_overrides_percent_and_flag_still_controls(self):
        book=self.position()
        book.configure('SOXL-USDT-SWAP',stop_loss_enabled=True,stop_loss_pct=0,stop_loss_amount=2)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',99)[0],'HOLD')
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',97)[0],'TRIGGER_STOP_LOSS')
        book.configure('SOXL-USDT-SWAP',stop_loss_enabled=False)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',90)[0],'HOLD')

    def test_signed_recovery_profit_and_amount_profit(self):
        book=self.position()
        book.configure('SOXL-USDT-SWAP',fixed_take_profit_pct=-.1)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',98)[0],'HOLD')
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',100)[0],'TRIGGER_FIXED_TP')
        book.configure('SOXL-USDT-SWAP',fixed_take_profit_amount=5)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',101)[0],'HOLD')
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',106)[0],'TRIGGER_FIXED_TP')

    def test_trailing_amount_activation_and_retrace(self):
        book=self.position()
        book.configure('SOXL-USDT-SWAP',trailing_enabled=True,trailing_activation_amount=2,trailing_retrace_amount=1)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',101)[0],'HOLD')
        self.assertFalse(book.ledgers['SOXL-USDT-SWAP'].trailing_active)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',105)[0],'HOLD')
        self.assertTrue(book.ledgers['SOXL-USDT-SWAP'].trailing_active)
        self.assertEqual(book.evaluate_risk('SOXL-USDT-SWAP',103)[0],'TRIGGER_TRAILING_TP')

    def test_invalid_settings_are_atomic(self):
        book=self.position()
        for value in (float('nan'),float('inf'),-1,True):
            with self.assertRaises(ValueError):book.configure('SOXL-USDT-SWAP',stop_loss_enabled=True,stop_loss_amount=value)
            self.assertFalse(book.ledgers['SOXL-USDT-SWAP'].stop_loss_enabled)

if __name__=='__main__':unittest.main()
