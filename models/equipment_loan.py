import math
from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class EquipmentLoan(models.Model):
    _name = "equipment.loan"
    _description = "Equipment Loan"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "date_start desc, id desc"

    OVERDUE_ACTIVITY_SUMMARY = "Return overdue equipment"

    name = fields.Char(
        required=True, copy=False, index=True, default="New",
    )
    item_id = fields.Many2one(
        "equipment.item", required=True, ondelete="restrict", check_company=True,
    )
    borrower_id = fields.Many2one(
        "res.users",
        required=True,
        default=lambda self: self.env.user,
        tracking=True,
        check_company=True,
    )
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True,
    )
    date_start = fields.Datetime(required=True, default=fields.Datetime.now)
    date_due = fields.Datetime(required=True, tracking=True)
    date_return = fields.Datetime(copy=False, readonly=True)
    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("confirmed", "Confirmed"),
            ("returned", "Returned"),
            ("cancelled", "Cancelled"),
        ],
        default="draft",
        copy=False,
        index=True,
        tracking=True,
    )
    notes = fields.Html()
    is_overdue = fields.Boolean(
        copy=False,
        index=True,
        help="Set by the daily scheduled action, never by hand.",
    )
    days_late = fields.Integer(compute="_compute_days_late")
    penalty_amount = fields.Monetary(
        compute="_compute_penalty_amount", currency_field="currency_id",
    )
    currency_id = fields.Many2one(
        "res.currency", related="item_id.currency_id", store=True, readonly=True,
    )
    duration_days = fields.Integer(
        compute="_compute_duration_days",
        store=True,
        help="Days the item is out: until the return date, or until the due date while still out.",
    )

    # The overlap check and the cron both filter on item + state + due date.
    _item_state_due_idx = models.Index("(item_id, state, date_due)")
    _borrower_state_idx = models.Index("(borrower_id, state)")

    # Generate a unique reference for new loans.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("name") or vals["name"] == "New":
                vals["name"] = (
                    self.env["ir.sequence"].next_by_code("equipment.loan")
                    or "New"
                )
        return super().create(vals_list)

    # ------------------------------------------------------------------
    # Computed fields
    # ------------------------------------------------------------------

    # Calculate the whole days a loan is late.
    def _late_days(self):
        """Whole days late, measured against the return date or against now while still out."""
        self.ensure_one()
        if not self.date_due:
            return 0
        end = self.date_return or fields.Datetime.now()
        return max(0, math.ceil((end - self.date_due).total_seconds() / 86400))

    # Calculate the days an item has been out.
    def _loan_duration(self):
        """Days the item is out, using the due date while the loan is still open."""
        self.ensure_one()
        end = self.date_return or self.date_due
        if not (self.date_start and end):
            return 0
        return max(0, math.ceil((end - self.date_start).total_seconds() / 86400))

    # Compute the number of late days.
    @api.depends("date_return", "date_due")
    def _compute_days_late(self):
        for loan in self:
            loan.days_late = loan._late_days()

    # Compute the late-return penalty.
    @api.depends("date_return", "date_due", "item_id", "item_id.daily_rate")
    def _compute_penalty_amount(self):
        for loan in self:
            loan.penalty_amount = loan._late_days() * loan.item_id.daily_rate

    # Compute the duration of each loan.
    @api.depends("date_start", "date_due", "date_return")
    def _compute_duration_days(self):
        for loan in self:
            loan.duration_days = loan._loan_duration()

    # ------------------------------------------------------------------
    # Constraints
    # ------------------------------------------------------------------

    # Find confirmed loans that overlap this loan period.
    def _overlapping_loans(self):
        """Loans of the same item that are confirmed, not returned and overlap the period."""
        self.ensure_one()
        return self.search(
            [
                ("id", "!=", self.id),
                ("company_id", "=", self.company_id.id),
                ("item_id", "=", self.item_id.id),
                ("state", "=", "confirmed"),
                ("date_return", "=", False),
                ("date_start", "<", self.date_due),
                ("date_due", ">", self.date_start),
            ],
        )

    # Prevent overlapping confirmed loans for the same item.
    @api.constrains("item_id", "date_start", "date_due", "state")
    def _check_no_overlapping_loan(self):
        for loan in self:
            if loan.state != "confirmed" or not (loan.item_id and loan.date_start and loan.date_due):
                continue
            conflicts = loan._overlapping_loans()
            if conflicts:
                raise ValidationError(
                    _(
                        "%(item)s is already booked by %(loans)s from %(start)s to %(due)s.",
                        item=loan.item_id.display_name,
                        loans=", ".join(conflicts.mapped("name")),
                        start=fields.Datetime.to_string(loan.date_start),
                        due=fields.Datetime.to_string(loan.date_due),
                    ),
                )

    # Require the due date to follow the start date.
    @api.constrains("date_start", "date_due")
    def _check_date_order(self):
        for loan in self:
            if loan.date_start and loan.date_due and loan.date_due <= loan.date_start:
                raise ValidationError(_("The due date must be strictly after the start date."))

    # Require a return date for returned loans.
    @api.constrains("state", "date_return")
    def _check_returned_has_date(self):
        for loan in self:
            if loan.state == "returned" and not loan.date_return:
                raise ValidationError(_("A returned loan must have a return date."))

    # ------------------------------------------------------------------
    # Workflow
    # ------------------------------------------------------------------

    # Confirm the selected draft loans.
    def action_confirm(self):
        for loan in self:
            if loan.state != "draft":
                raise UserError(
                    _("%(name)s cannot be confirmed because it is %(state)s.", name=loan.name, state=loan.state),
                )
        self.write({"state": "confirmed"})
        return True

    # Open the return wizard for the selected loans.
    def action_return(self):
        for loan in self:
            if loan.state != "confirmed":
                raise UserError(
                    _("%(name)s cannot be returned because it is %(state)s.", name=loan.name, state=loan.state),
                )
        action = self.env.ref(
            "porcelia_equipment_loan.action_equipment_loan_return_wizard",
        ).read()[0]
        action["context"] = {
            "active_model": "equipment.loan",
            "active_ids": self.ids,
        }

        return action

    # Cancel loans that have not been returned.
    def action_cancel(self):
        for loan in self:
            if loan.state == "returned":
                raise UserError(
                    _("%(name)s cannot be cancelled because it has already been returned.", name=loan.name),
                )
            if loan.state == "cancelled":
                raise UserError(_("%(name)s is already cancelled.", name=loan.name))
        self.write({"state": "cancelled", "is_overdue": False})
        return True

    # Reset selected loans to draft.
    def action_draft(self):
        for loan in self:
            if loan.state == "draft":
                raise UserError(_("%(name)s is already a draft.", name=loan.name))
        self.write({"state": "draft", "date_return": False, "is_overdue": False})
        return True

    # Allow deletion only for draft or cancelled loans.
    @api.ondelete(at_uninstall=False)
    def _unlink_except_draft_or_cancelled(self):
        if any(loan.state not in ("draft", "cancelled") for loan in self):
            raise UserError(_("Only draft or cancelled loans can be deleted."))

    # ------------------------------------------------------------------
    # Scheduled action
    # ------------------------------------------------------------------

    # Find open loans that the daily job must flag.
    @api.model
    def _cron_pending_overdue_loans(self):
        """Return the open loans the daily job is about to flag."""
        return self.search(
            [
                ("state", "=", "confirmed"),
                ("is_overdue", "=", False),
                ("date_return", "=", False),
                ("date_due", "<", fields.Datetime.now()),
            ],
        )

    # Flag overdue loans and schedule borrower reminders.
    @api.model
    def _cron_flag_overdue_loans(self):
        """Flag loans that are past due and schedule a reminder on the borrower.

        Only loans that are not flagged yet are picked, so running the job again
        does not post a second message nor schedule a second activity.
        """
        today = fields.Date.context_today(self)
        loans = self._cron_pending_overdue_loans()
        for loan in loans:
            loan.is_overdue = True
            loan.message_post(
                body=_("This loan was due on %s and is still open.", loan.date_due),
            )
            already_scheduled = loan.activity_ids.filtered(
                lambda activity: activity.summary == self.OVERDUE_ACTIVITY_SUMMARY
                and activity.date_deadline >= today,
            )
            if not already_scheduled:
                loan.activity_schedule(
                    "mail.mail_activity_data_todo",
                    summary=self.OVERDUE_ACTIVITY_SUMMARY,
                    user_id=loan.borrower_id.id,
                    date_deadline=today + timedelta(days=1),
                )
        return len(loans)
