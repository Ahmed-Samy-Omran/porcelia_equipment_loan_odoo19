from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError


class EquipmentLoanReturnWizard(models.TransientModel):
    _name = "equipment.loan.return.wizard"
    _description = "Return Equipment"

    date_return = fields.Datetime(required=True, default=fields.Datetime.now)
    condition_score = fields.Integer(
        required=True,
        default=lambda self: self._default_condition_score(),
        help="Condition of the returned equipment, from 0 (broken) to 100 (as new).",
    )
    note = fields.Text()

    @api.model
    def _default_condition_score(self):
        loans = self._selected_loans()
        return loans[:1].item_id.condition_score or 100

    def _selected_loans(self):
        """Loans picked in the list view, or the ones the caller passed in the context."""
        loan_ids = self.env.context.get("active_ids") or []
        return self.env["equipment.loan"].browse(loan_ids).exists()

    def action_confirm_return(self):
        loans = self._selected_loans()
        if not loans:
            raise UserError(_("No loan to return."))
        for loan in loans:
            if loan.state != "confirmed":
                raise UserError(
                    _("%(name)s cannot be returned because it is %(state)s.", name=loan.name, state=loan.state),
                )
            if self.date_return and loan.date_start and self.date_return < loan.date_start:
                raise ValidationError(
                    _("The return date cannot be before the start date of %(name)s.", name=loan.name),
                )

        loans.write({"date_return": self.date_return, "state": "returned", "is_overdue": False})
        message = self.note or _("Equipment returned in good condition.")
        for loan in loans:
            loan.item_id.condition_score = self.condition_score
            loan.message_post(body=message)

        return {"type": "ir.actions.act_window_close"}
