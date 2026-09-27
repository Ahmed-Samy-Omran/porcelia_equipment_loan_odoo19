{
    "name": "Porcelia Equipment Loan Manager",
    "summary": "Borrow, track and return company equipment",
    # Odoo renders module descriptions as reStructuredText; README.md stays Markdown for Git hosting.
    "description": """
Equipment Loan Manager
======================

Borrow, track, and return company equipment.
""",
    "version": "19.0.1.0.0",
    "category": "Inventory/Equipment",
    "license": "LGPL-3",
    "author": "Porcelia - Technical Team",
    "depends": ["base", "mail"],
    "data": [
        "security/equipment_groups.xml",
        "security/ir.model.access.csv",
        "security/equipment_record_rules.xml",
        "data/equipment_sequence_data.xml",
        "views/equipment_category_views.xml",
        "views/equipment_item_views.xml",
        "report/ir_actions_report.xml",
        "views/equipment_loan_views.xml",
        "wizard/equipment_loan_return_wizard_views.xml",
        "views/equipment_menus.xml",
        "views/res_users_views.xml",
    ],
    "demo": ["data/equipment_demo.xml"],
    "installable": True,
    "application": True,
}
