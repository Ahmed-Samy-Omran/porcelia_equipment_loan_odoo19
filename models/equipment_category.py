from odoo import api, fields, models


class EquipmentCategory(models.Model):
    _name = "equipment.category"
    _description = "Equipment Category"
    _parent_name = "parent_id"
    _parent_store = True
    _order = "complete_name, id"

    name = fields.Char(required=True, translate=True)
    complete_name = fields.Char(compute="_compute_complete_name", recursive=True, store=True)
    parent_id = fields.Many2one("equipment.category", ondelete="restrict", index=True)
    parent_path = fields.Char(index=True)
    child_ids = fields.One2many("equipment.category", "parent_id")
    item_ids = fields.One2many("equipment.item", "category_id")
    item_count = fields.Integer(compute="_compute_item_count")

    # Build the full category name from its parent hierarchy.name -> NAME OF CATEGORY 
    @api.depends("name", "parent_id.complete_name")
    def _compute_complete_name(self):
        for category in self:
            if category.parent_id:  #ELECTRONICS / LAPTOPS
                category.complete_name = f"{category.parent_id.complete_name} / {category.name}"
            else:
                category.complete_name = category.name

    # Count the items directly assigned to each category.
    @api.depends("item_ids")
    def _compute_item_count(self):
        # One grouped query for the whole recordset instead of one count per category.
        counts = dict(
            self.env["equipment.item"]._read_group(
                [("category_id", "in", self.ids)], #DOMAIN
                ["category_id"], #GROUPING
                ["__count"], #AGGREGATE
            ),
        )
        for category in self:
            category.item_count = counts.get(category, 0) #INCASE there are no items in the category, it will return 0 instead of None. laptops_category: 4,

    # Open the items belonging to this category.
    def action_view_items(self):
        self.ensure_one()

        action = self.env.ref(
            "porcelia_equipment_loan.action_equipment_item_by_category",
        ).read()[0]  # READ() CONVERTS THE RESULT TO A LIST OF DICTIONARY, so we take the first one.
        action["domain"] = [("category_id", "=", self.id)]
        action["context"] = {
            "default_category_id": self.id,
        }

        return action
