from odoo import _, api, fields, models
from odoo.exceptions import UserError


class EquipmentItem(models.Model):
    _name = "equipment.item"
    _description = "Equipment Item"
    _inherit = ["mail.thread"]
    _order = "name, id"

    name = fields.Char(required=True)
    code = fields.Char(
        required=True,
        copy=False,
        index=True,
        default="New",
    )
    category_id = fields.Many2one("equipment.category", ondelete="restrict")
    image_1920 = fields.Image(copy=True)
    active = fields.Boolean(default=True)
    company_id = fields.Many2one(
        "res.company", required=True, default=lambda self: self.env.company, index=True,
    )
    daily_rate = fields.Monetary(currency_field="currency_id")
    currency_id = fields.Many2one(
        "res.currency", required=True, default=lambda self: self.env.company.currency_id,
    )
    condition_score = fields.Integer(
        string="Condition Score",
        required=True,
        default=100,
        tracking=True,
        help="Condition of the item, from 0 (broken) to 100 (as new).",
    )
    in_maintenance = fields.Boolean(
        help="The item is being serviced and cannot be borrowed.",
    )
    scrapped = fields.Boolean(help="The item is retired and cannot be borrowed.")
    state = fields.Selection(
        [
            ("available", "Available"),
            ("on_loan", "On Loan"),
            ("maintenance", "Maintenance"),
            ("scrapped", "Scrapped"),
        ],
        compute="_compute_state",
        store=True,
        copy=False,
        index=True,
        tracking=True,
    )
    loan_ids = fields.One2many("equipment.loan", "item_id")
    loan_count = fields.Integer(compute="_compute_loan_count")
    total_days_on_loan = fields.Integer(
        compute="_compute_total_days_on_loan",
        help="Total days the item has been out, on every loan but drafts and cancellations.",
    )

    _code_unique = models.UniqueIndex(
        "(code, company_id)", "The equipment code must be unique per company.",
    )
    _condition_score_range = models.Constraint(
        "CHECK(condition_score >= 0 AND condition_score <= 100)",
        "The condition score must be between 0 and 100.",
    )
    _daily_rate_positive = models.Constraint(
        "CHECK(daily_rate >= 0)", "The daily rate cannot be negative.",
    )

    # Generate a unique code for new equipment items.
    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if not vals.get("code") or vals["code"] == "New":
                vals["code"] = (
                    self.env["ir.sequence"].next_by_code("equipment.item")
                    or "New"
                )
        return super().create(vals_list)

    # Compute the current availability state of each item.
    @api.depends("scrapped", "in_maintenance", "loan_ids.state", "loan_ids.date_return")
    def _compute_state(self):
        # Maintenance and scrapping are manual, the loan status is derived from the
        # confirmed loans that have not been returned yet.
        on_loan_ids = {
            item.id
            for item, _count in self.env["equipment.loan"]._read_group(
                [
                    ("item_id", "in", self.ids),
                    ("state", "=", "confirmed"),
                    ("date_return", "=", False),
                ],
                ["item_id"],
                ["__count"],
            )
        }
        for item in self:
            if item.scrapped:
                item.state = "scrapped"
            elif item.in_maintenance:
                item.state = "maintenance"
            elif item.id in on_loan_ids:
                item.state = "on_loan"
            else:
                item.state = "available"

    # Count loans except drafts and cancellations.
    @api.depends("loan_ids")
    def _compute_loan_count(self):
        counts = dict(
            self.env["equipment.loan"]._read_group(
                [
                    ("item_id", "in", self.ids),
                    ("state", "not in", ("draft", "cancelled")),
                ],
                ["item_id"],
                ["__count"],
            ),
        )
        for item in self:
            item.loan_count = counts.get(item, 0)

    # Sum the days each item has been on loan.
    @api.depends("loan_ids.duration_days")
    def _compute_total_days_on_loan(self):
        # duration_days is stored on the loan, so the total is a single SUM in the
        # database rather than a Python loop over every loan.
        totals = dict(
            self.env["equipment.loan"]._read_group(
                [
                    ("item_id", "in", self.ids),
                    ("state", "not in", ("draft", "cancelled")),
                ],
                ["item_id"],
                ["duration_days:sum"],
            ),
        )
        for item in self:
            item.total_days_on_loan = totals.get(item, 0)

    # Open the loans belonging to this equipment item.
    def action_view_loans(self):
        self.ensure_one()

        action = self.env.ref(
            "porcelia_equipment_loan.action_equipment_loan_by_item",
        ).read()[0]
        action["domain"] = [("item_id", "=", self.id)]
        action["context"] = {
            "default_item_id": self.id,
        }

        return action

    # Toggle the maintenance status of the selected items.
    def action_toggle_maintenance(self):
        for item in self:
            if item.state == "on_loan":
                raise UserError(
                    _("%(name)s is currently on loan and cannot go to maintenance.", name=item.name),
                )
        self.in_maintenance = not self.in_maintenance
        return True

    # Toggle the scrapped status of the selected items.
    def action_toggle_scrapped(self):
        for item in self:
            if item.state == "on_loan":
                raise UserError(
                    _("%(name)s is currently on loan and cannot be scrapped.", name=item.name),
                )
        self.scrapped = not self.scrapped
        return True
