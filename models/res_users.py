from odoo import _, fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    equipment_loan_count = fields.Integer(compute="_compute_equipment_loan_count")

    def _compute_equipment_loan_count(self):
        # No sudo: the count goes through the record rule, so a plain equipment user
        # only ever counts their own loans.
        counts = dict(
            self.env["equipment.loan"]._read_group(
                [("borrower_id", "in", self.ids)], ["borrower_id"], ["__count"],
            ),
        )
        for user in self:
            user.equipment_loan_count = counts.get(user, 0)

    def action_view_equipment_loans(self):
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Loans"),
            "res_model": "equipment.loan",
            "view_mode": "list,form",
            "domain": [("borrower_id", "=", self.id)],
            "context": {"create": False},
        }
