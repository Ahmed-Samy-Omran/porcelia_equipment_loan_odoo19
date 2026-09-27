from odoo import fields, models


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

    # Open the loans belonging to this user.
    def action_view_equipment_loans(self):
        self.ensure_one()

        action = self.env.ref(
            "porcelia_equipment_loan.action_equipment_loan_by_borrower",
        ).read()[0]
        action["domain"] = [("borrower_id", "=", self.id)]
        action["context"] = {
            "create": False,
        }

        return action
