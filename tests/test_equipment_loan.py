from datetime import timedelta

from psycopg2.errors import UniqueViolation

from odoo import fields
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tests.common import TransactionCase, mute_logger


class TestEquipmentLoan(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user_group = cls.env.ref("porcelia_equipment_loan.group_equipment_user")
        cls.manager_group = cls.env.ref("porcelia_equipment_loan.group_equipment_manager")
        cls.borrower = cls._create_user("equipment.borrower", cls.user_group)
        cls.other_borrower = cls._create_user("equipment.other", cls.user_group)
        cls.manager = cls._create_user("equipment.manager", cls.manager_group)
        cls.item = cls._create_item("Loan laptop", 10.0)
        cls.other_item = cls._create_item("Loan laser", 20.0)

    @classmethod
    def _create_user(cls, login, group):
        return cls.env["res.users"].create(
            {
                "name": login,
                "login": login,
                "group_ids": [(6, 0, [group.id])],
            },
        )

    @classmethod
    def _create_item(cls, name, rate):
        return cls.env["equipment.item"].create(
            {"name": name, "daily_rate": rate, "condition_score": 100},
        )

    def _create_loan(self, item=None, borrower=None, days_overdue=0, length=4, **vals):
        now = fields.Datetime.now()
        return self.env["equipment.loan"].create(
            {
                "item_id": (item or self.item).id,
                "borrower_id": (borrower or self.borrower).id,
                "date_start": now - timedelta(days=length + days_overdue),
                "date_due": now - timedelta(days=days_overdue),
                **vals,
            },
        )

    def _return(self, loans, when, **vals):
        wizard = (
            self.env["equipment.loan.return.wizard"]
            .with_context(active_ids=loans.ids)
            .create({"date_return": when, **vals})
        )
        return wizard.action_confirm_return()

    # ------------------------------------------------------------------
    # Overlap rule
    # ------------------------------------------------------------------

    def test_overlapping_confirmation_is_rejected(self):
        now = fields.Datetime.now()
        self._create_loan(self.item, days_overdue=0, length=6).action_confirm()
        overlapping = self.env["equipment.loan"].create(
            {
                "item_id": self.item.id,
                "borrower_id": self.borrower.id,
                "date_start": now - timedelta(days=1),
                "date_due": now + timedelta(days=1),
            },
        )
        with self.assertRaises(ValidationError):
            overlapping.action_confirm()
        self.assertEqual(overlapping.state, "draft")

    def test_touching_periods_do_not_overlap(self):
        now = fields.Datetime.now()
        self._create_loan(self.item, days_overdue=1, length=3).action_confirm()
        following = self.env["equipment.loan"].create(
            {
                "item_id": self.item.id,
                "borrower_id": self.borrower.id,
                "date_start": now - timedelta(days=1),
                "date_due": now + timedelta(days=2),
            },
        )
        following.action_confirm()
        self.assertEqual(following.state, "confirmed")

    def test_draft_loan_does_not_block(self):
        now = fields.Datetime.now()
        self._create_loan(self.item, days_overdue=1)
        other = self.env["equipment.loan"].create(
            {
                "item_id": self.item.id,
                "borrower_id": self.borrower.id,
                "date_start": now - timedelta(days=1),
                "date_due": now + timedelta(days=2),
            },
        )
        other.action_confirm()
        self.assertEqual(other.state, "confirmed")

    def test_due_date_must_be_after_start_date(self):
        with self.assertRaises(ValidationError):
            self.env["equipment.loan"].create(
                {
                    "item_id": self.item.id,
                    "borrower_id": self.borrower.id,
                    "date_start": fields.Datetime.now(),
                    "date_due": fields.Datetime.now(),
                },
            )

    # ------------------------------------------------------------------
    # Penalty
    # ------------------------------------------------------------------

    def test_late_return_penalty(self):
        now = fields.Datetime.now()
        loan = self._create_loan(days_overdue=4, state="confirmed")
        # Due 4 days ago, returned 1 day and 1 hour ago: 2 days and 23 hours late,
        # which rounds up to 3 whole days.
        self._return(loan, now - timedelta(days=1, hours=1))
        self.assertEqual(loan.state, "returned")
        self.assertEqual(loan.days_late, 3)
        self.assertAlmostEqual(loan.penalty_amount, 3 * self.item.daily_rate, places=2)

    def test_early_return_has_no_penalty(self):
        now = fields.Datetime.now()
        loan = self._create_loan(days_overdue=-2, state="confirmed")
        self._return(loan, now)
        self.assertEqual(loan.days_late, 0)
        self.assertEqual(loan.penalty_amount, 0)

    def test_on_time_return_has_no_penalty(self):
        now = fields.Datetime.now()
        loan = self._create_loan(days_overdue=-1, state="confirmed")
        self._return(loan, now - timedelta(hours=2))
        self.assertEqual(loan.days_late, 0)
        self.assertEqual(loan.penalty_amount, 0)

    def test_open_overdue_loan_shows_its_lateness(self):
        loan = self._create_loan(days_overdue=3, state="confirmed")
        self.assertEqual(loan.days_late, 3)
        self.assertAlmostEqual(loan.penalty_amount, 3 * self.item.daily_rate, places=2)
        self.assertEqual(loan.duration_days, 4)

    # ------------------------------------------------------------------
    # Security
    # ------------------------------------------------------------------

    def test_user_cannot_read_another_users_loan(self):
        loan = self._create_loan(borrower=self.other_borrower, state="confirmed")
        with self.assertRaises(AccessError):
            loan.with_user(self.borrower).read(["name"])
        visible = self.env["equipment.loan"].with_user(self.borrower).search([])
        self.assertNotIn(loan, visible)

    def test_user_reads_own_loan(self):
        loan = self._create_loan(borrower=self.borrower, state="confirmed")
        self.assertTrue(loan.with_user(self.borrower).read(["name"]))
        self.assertIn(loan, self.env["equipment.loan"].with_user(self.borrower).search([]))

    def test_manager_sees_every_loan(self):
        loan = self._create_loan(borrower=self.other_borrower, state="confirmed")
        self.assertIn(loan, self.env["equipment.loan"].with_user(self.manager).search([]))

    def test_user_cannot_delete_item(self):
        with self.assertRaises(AccessError):
            self.item.with_user(self.borrower).unlink()

    def test_user_cannot_delete_loan(self):
        loan = self._create_loan(borrower=self.borrower)
        with self.assertRaises(AccessError):
            loan.with_user(self.borrower).unlink()

    # ------------------------------------------------------------------
    # Workflow and deletion guard
    # ------------------------------------------------------------------

    def test_workflow_transitions(self):
        loan = self._create_loan()
        self.assertEqual(loan.state, "draft")
        with self.assertRaises(UserError):
            loan.action_draft()
        loan.action_confirm()
        self.assertEqual(loan.state, "confirmed")
        with self.assertRaises(UserError):
            loan.action_confirm()
        loan.action_cancel()
        self.assertEqual(loan.state, "cancelled")
        with self.assertRaises(UserError):
            loan.action_cancel()
        loan.action_draft()
        self.assertEqual(loan.state, "draft")

    def test_returning_a_draft_loan_is_rejected(self):
        with self.assertRaises(UserError):
            self._create_loan().action_return()

    def test_returned_loan_cannot_be_cancelled(self):
        loan = self._create_loan(state="confirmed")
        self._return(loan, fields.Datetime.now())
        self.assertEqual(loan.state, "returned")
        with self.assertRaises(UserError):
            loan.action_cancel()

    def test_reset_to_draft_clears_the_return(self):
        loan = self._create_loan(state="confirmed")
        self._return(loan, fields.Datetime.now())
        loan.action_draft()
        self.assertEqual(loan.state, "draft")
        self.assertFalse(loan.date_return)
        self.assertEqual(loan.penalty_amount, 0)

    def test_only_draft_or_cancelled_loans_can_be_deleted(self):
        confirmed = self._create_loan(state="confirmed")
        with self.assertRaises(UserError):
            confirmed.unlink()
        confirmed.action_cancel()
        confirmed.unlink()
        self._create_loan().unlink()

    def test_item_state_follows_confirmed_loans(self):
        self.assertEqual(self.item.state, "available")
        loan = self._create_loan(state="confirmed")
        self.assertEqual(self.item.state, "on_loan")
        self._return(loan, fields.Datetime.now())
        self.assertEqual(self.item.state, "available")

    def test_maintenance_and_scrapped_win_over_loans(self):
        self.item.in_maintenance = True
        self.assertEqual(self.item.state, "maintenance")
        self.item.in_maintenance = False
        self.item.scrapped = True
        self.assertEqual(self.item.state, "scrapped")

    # ------------------------------------------------------------------
    # Wizard
    # ------------------------------------------------------------------

    def test_wizard_returns_several_loans_at_once(self):
        first = self._create_loan(self.item, state="confirmed")
        second = self._create_loan(self.other_item, state="confirmed")
        action = self._return(first | second, fields.Datetime.now(), condition_score=60, note="Scratched")
        self.assertEqual(action, {"type": "ir.actions.act_window_close"})
        for loan in first | second:
            self.assertEqual(loan.state, "returned")
            self.assertTrue(loan.date_return)
            self.assertEqual(loan.item_id.condition_score, 60)
            self.assertTrue(any("Scratched" in (msg.body or "") for msg in loan.message_ids))
        self.assertEqual(self.item.condition_score, 60)
        self.assertEqual(self.other_item.condition_score, 60)

    def test_wizard_refuses_a_loan_that_is_not_confirmed(self):
        loan = self._create_loan()
        with self.assertRaises(UserError):
            self._return(loan, fields.Datetime.now())

    def test_wizard_refuses_a_return_before_the_loan_started(self):
        now = fields.Datetime.now()
        loan = self._create_loan(state="confirmed")
        with self.assertRaises(ValidationError):
            self._return(loan, now - timedelta(days=10))

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    def test_cron_flags_overdue_loans_once(self):
        loan = self._create_loan(days_overdue=3, state="confirmed")
        messages_before = len(loan.message_ids)
        loans = self.env["equipment.loan"]
        pending = loans._cron_pending_overdue_loans()

        self.assertIn(loan, pending)
        self.assertEqual(loans._cron_flag_overdue_loans(), len(pending))
        self.assertTrue(loan.is_overdue)
        self.assertEqual(len(loan.activity_ids), 1)
        self.assertEqual(len(loan.message_ids), messages_before + 1)

        for _ in range(4):
            self.assertEqual(loans._cron_flag_overdue_loans(), 0)
        self.assertEqual(len(loan.activity_ids), 1)
        self.assertEqual(len(loan.message_ids), messages_before + 1)

    def test_cron_does_not_duplicate_activity_when_the_flag_is_reset(self):
        loan = self._create_loan(days_overdue=3, state="confirmed")
        loans = self.env["equipment.loan"]
        loans._cron_flag_overdue_loans()
        loan.is_overdue = False
        loans._cron_flag_overdue_loans()
        self.assertEqual(len(loan.activity_ids), 1)

    def test_cron_ignores_returned_and_open_loans(self):
        returned = self._create_loan(days_overdue=3, state="confirmed")
        self._return(returned, fields.Datetime.now() - timedelta(days=1))
        on_time = self._create_loan(self.other_item, days_overdue=-2, state="confirmed")

        self.env["equipment.loan"]._cron_flag_overdue_loans()

        self.assertFalse(returned.is_overdue)
        self.assertFalse(on_time.is_overdue)
        self.assertFalse(returned.activity_ids)

    # ------------------------------------------------------------------
    # Data integrity
    # ------------------------------------------------------------------

    @mute_logger("odoo.sql_db")
    def test_codes_come_from_a_sequence_and_stay_unique(self):
        codes = {self.item.code, self.other_item.code}
        self.assertEqual(len(codes), 2)
        self.assertRegex(self.item.code, r"^EQ/\d{4}/\d{4}$")
        with self.assertRaises(UniqueViolation):
            self.env["equipment.item"].create(
                {"name": "Clash", "code": self.item.code, "daily_rate": 1.0},
            )

    def test_loan_reference_comes_from_a_sequence(self):
        loan = self._create_loan()
        self.assertRegex(loan.name, r"^LOAN/\d{4}/\d{4}$")

    def test_aggregates_are_computed_in_one_query(self):
        loan = self._create_loan(state="confirmed", length=6)
        items = self.item | self.other_item
        self.assertEqual(items.mapped("loan_count"), [1, 0])
        self.assertEqual(items.mapped("total_days_on_loan"), [6, 0])
        self.assertTrue(loan.exists())

    def test_category_item_count(self):
        category = self.env["equipment.category"].create({"name": "Counted"})
        self.env["equipment.item"].create(
            {"name": "In category", "category_id": category.id, "daily_rate": 1.0},
        )
        self.assertEqual(category.item_count, 1)
        self.assertEqual(category.complete_name, "Counted")

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def test_report_renders_every_selected_loan(self):
        first = self._create_loan(state="confirmed")
        second = self._create_loan(self.other_item, state="confirmed")
        report = self.env["ir.actions.report"]

        html, report_type = report._render_qweb_html(
            "porcelia_equipment_loan.action_report_equipment_loan",
            (first | second).ids,
        )

        self.assertEqual(report_type, "html")
        content = html.decode() if isinstance(html, bytes) else html
        self.assertIn(first.name, content)
        self.assertIn(second.name, content)

    def test_report_of_another_users_loan_is_forbidden(self):
        foreign = self._create_loan(state="confirmed", borrower=self.other_borrower)
        with self.assertRaises(AccessError):
            self.env["ir.actions.report"].with_user(self.borrower)._render_qweb_html(
                "porcelia_equipment_loan.action_report_equipment_loan",
                foreign.ids,
            )
